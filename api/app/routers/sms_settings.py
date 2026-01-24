# api/app/routers/sms_settings.py
from datetime import datetime
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session
import requests

from ..shared import APIRouter
from ..database import get_db
from ..models import AccountSmsSettings, SmsCreditLedger, SmsPricingSettings
from ..crypto_secrets import encrypt_secret
from .auth import require_user
router = APIRouter(prefix="/api/sms", tags=["sms_settings"])

class SmsSettingsOut(BaseModel):
    enabled: bool
    twilio_phone_number: Optional[str] = None
    twilio_phone_sid: Optional[str] = None
    forwarding_enabled: bool
    forward_to_phone: Optional[str] = None
    bundle_size: int
    credits_balance: int
    free_credits: int
    terms_accepted_at: Optional[datetime] = None
    terms_version: Optional[str] = None

class SmsSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    twilio_phone_number: Optional[str] = None
    twilio_phone_sid: Optional[str] = None
    forwarding_enabled: Optional[bool] = None
    forward_to_phone: Optional[str] = None
    bundle_size: Optional[int] = Field(None, ge=100, le=100000)
    credits_balance: Optional[int] = Field(None, ge=0)
    free_credits: Optional[int] = Field(None, ge=0)

class SmsTermsIn(BaseModel):
    accepted: bool
    terms_version: Optional[str] = None
    pricing_snapshot: Optional[dict] = None
    country: Optional[str] = None

class PricingOut(BaseModel):
    sms_starting_credits: int
    sms_monthly_number_cost: int
    sms_send_cost: int
    sms_forward_cost: int
    sms_suspend_after_days: int


def _ensure_sms_settings(db: Session, user_id: int) -> AccountSmsSettings:
    row = (
        db.query(AccountSmsSettings)
        .filter(AccountSmsSettings.user_id == user_id)
        .first()
    )
    if row:
        return row
    row = AccountSmsSettings(user_id=user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def _calculate_credit_balance(db: Session, user_id: int) -> tuple[bool, int]:
    has_entries = (
        db.query(SmsCreditLedger.id)
        .filter(SmsCreditLedger.user_id == user_id)
        .first()
        is not None
    )
    if not has_entries:
        return False, 0

    total = (
        db.query(
            func.coalesce(
                func.sum(
                    case(
                        (SmsCreditLedger.entry_type == "credit", SmsCreditLedger.amount),
                        else_=-SmsCreditLedger.amount,
                    )
                ),
                0,
            )
        )
        .filter(SmsCreditLedger.user_id == user_id)
        .scalar()
    )
    return True, int(total or 0)

def _ensure_pricing(db: Session) -> SmsPricingSettings:
    row = db.query(SmsPricingSettings).order_by(SmsPricingSettings.id.asc()).first()
    if row:
        return row
    row = SmsPricingSettings(
        sms_starting_credits=1000,
        sms_monthly_number_cost=100,
        sms_send_cost=5,
        sms_forward_cost=5,
        sms_suspend_after_days=14,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def _build_pricing_snapshot(row: SmsPricingSettings) -> dict:
    return {
        "sms_starting_credits": row.sms_starting_credits,
        "sms_monthly_number_cost": row.sms_monthly_number_cost,
        "sms_send_cost": row.sms_send_cost,
        "sms_forward_cost": row.sms_forward_cost,
        "sms_suspend_after_days": row.sms_suspend_after_days,
    }

def _twilio_auth_headers(username: str, password: str) -> tuple[str, str]:
    return (username, password)

def _provision_twilio_number(
    *,
    country: str,
    webhook_base: str,
    account_sid: str,
    auth_sid: str,
    auth_secret: str,
    bundle_sid: Optional[str],
) -> dict:
    available_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/AvailablePhoneNumbers/"
        f"{country}/Mobile.json"
    )
    available_params = {
        "SmsEnabled": "true",
        "PageSize": 1,
    }
    r_available = requests.get(
        available_url,
        params=available_params,
        auth=_twilio_auth_headers(auth_sid, auth_secret),
        timeout=20,
    )
    r_available.raise_for_status()
    data = r_available.json()
    numbers = data.get("available_phone_numbers") or []
    if not numbers:
        raise HTTPException(status_code=400, detail="No available Twilio numbers for this country.")
    phone_number = numbers[0].get("phone_number")
    if not phone_number:
        raise HTTPException(status_code=502, detail="Twilio did not return a phone number.")

    inbound_url = f"{webhook_base.rstrip('/')}/api/sms/webhooks/inbound"
    status_url = f"{webhook_base.rstrip('/')}/api/sms/webhooks/status"
    purchase_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    purchase_payload = {
        "PhoneNumber": phone_number,
        "SmsUrl": inbound_url,
        "SmsMethod": "POST",
        "StatusCallback": status_url,
        "StatusCallbackMethod": "POST",
    }
    if bundle_sid:
        purchase_payload["BundleSid"] = bundle_sid
    r_purchase = requests.post(
        purchase_url,
        data=purchase_payload,
        auth=_twilio_auth_headers(auth_sid, auth_secret),
        timeout=20,
    )
    r_purchase.raise_for_status()
    purchase = r_purchase.json()
    return {
        "phone_number": purchase.get("phone_number") or phone_number,
        "phone_sid": purchase.get("sid"),
    }

def _ensure_twilio_subaccount(
    *,
    user_email: str,
    webhook_base: str,
    country: str,
    parent_bundle_sid: str,
) -> dict:
    master_sid = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
    api_key_sid = (os.getenv("TWILIO_API_KEY_SID", "") or "").strip()
    api_key_secret = (os.getenv("TWILIO_API_KEY_SECRET", "") or "").strip()
    if not master_sid or not api_key_sid or not api_key_secret:
        raise HTTPException(status_code=400, detail="Twilio API key credentials not configured.")
    if not parent_bundle_sid:
        raise HTTPException(status_code=400, detail="TWILIO_PARENT_BUNDLE_SID is not configured.")

    create_url = "https://api.twilio.com/2010-04-01/Accounts.json"
    payload = {"FriendlyName": f"RemindPay {user_email or 'Account'}"}
    r_create = requests.post(
        create_url,
        data=payload,
        auth=_twilio_auth_headers(api_key_sid, api_key_secret),
        timeout=20,
    )
    r_create.raise_for_status()
    data = r_create.json()
    sub_sid = data.get("sid")
    sub_token = data.get("auth_token")
    if not sub_sid or not sub_token:
        raise HTTPException(status_code=502, detail="Twilio did not return subaccount credentials.")

    clone_url = f"https://numbers.twilio.com/v2/RegulatoryCompliance/Bundles/{parent_bundle_sid}/Clones"
    clone_payload = {
        "targetAccountSid": sub_sid,
        "friendlyName": f"RemindPay {user_email or 'Account'} bundle",
        "moveToDraft": "false",
    }
    r_clone = requests.post(
        clone_url,
        data=clone_payload,
        auth=_twilio_auth_headers(api_key_sid, api_key_secret),
        timeout=20,
    )
    if not r_clone.ok:
        raise HTTPException(
            status_code=502,
            detail=f"Twilio bundle clone failed: {r_clone.status_code} {r_clone.text}",
        )
    clone_data = r_clone.json()
    bundle_sid = clone_data.get("bundle_sid") or clone_data.get("bundleSid")
    if not bundle_sid:
        raise HTTPException(status_code=502, detail="Twilio bundle clone did not return BundleSid.")

    provisioned = _provision_twilio_number(
        country=country,
        webhook_base=webhook_base,
        account_sid=sub_sid,
        auth_sid=api_key_sid,
        auth_secret=api_key_secret,
        bundle_sid=bundle_sid,
    )

    return {
        "subaccount_sid": sub_sid,
        "auth_token": sub_token,
        "bundle_sid": bundle_sid,
        "phone_number": provisioned.get("phone_number"),
        "phone_sid": provisioned.get("phone_sid"),
    }

@router.get("/pricing", response_model=PricingOut)
def get_pricing(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    row = _ensure_pricing(db)
    return PricingOut(**_build_pricing_snapshot(row))

@router.get("/settings", response_model=SmsSettingsOut)
def get_sms_settings(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _ensure_sms_settings(db, user.id)
    has_ledger, ledger_balance = _calculate_credit_balance(db, user.id)
    credits_balance = ledger_balance if has_ledger else (row.credits_balance or 0)

    return SmsSettingsOut(
        enabled=bool(row.enabled),
        twilio_phone_number=row.twilio_phone_number,
        twilio_phone_sid=row.twilio_phone_sid,
        forwarding_enabled=bool(row.forwarding_enabled),
        forward_to_phone=row.forward_to_phone,
        bundle_size=row.bundle_size or 1000,
        credits_balance=credits_balance,
        free_credits=row.free_credits or 0,
        terms_accepted_at=row.terms_accepted_at,
        terms_version=row.terms_version,
    )

@router.post("/enable", response_model=SmsSettingsOut)
def enable_sms(
    payload: SmsTermsIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    if not payload.accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Terms acceptance required")

    row = _ensure_sms_settings(db, user.id)
    pricing = _ensure_pricing(db)
    snapshot = payload.pricing_snapshot or _build_pricing_snapshot(pricing)
    webhook_base = (os.getenv("TWILIO_WEBHOOK_BASE_URL", "") or "").strip()
    country = (payload.country or os.getenv("TWILIO_DEFAULT_COUNTRY", "GB") or "GB").upper()
    if not webhook_base:
        raise HTTPException(status_code=400, detail="TWILIO_WEBHOOK_BASE_URL is not configured.")

    row.enabled = True
    row.terms_accepted_at = datetime.utcnow()
    row.terms_version = (payload.terms_version or "v1")[:32]
    row.terms_accepted_ip = request.client.host if request.client else None
    row.accepted_pricing_snapshot = snapshot

    parent_bundle_sid = (os.getenv("TWILIO_PARENT_BUNDLE_SID", "") or "").strip()

    if not row.twilio_subaccount_sid or not row.twilio_auth_token_enc or not row.twilio_bundle_sid:
        provisioned = _ensure_twilio_subaccount(
            user_email=user.email or "",
            webhook_base=webhook_base,
            country=country,
            parent_bundle_sid=parent_bundle_sid,
        )
        row.twilio_subaccount_sid = provisioned["subaccount_sid"]
        row.twilio_auth_token_enc = encrypt_secret(provisioned["auth_token"])
        row.twilio_bundle_sid = provisioned.get("bundle_sid")
        row.twilio_phone_number = provisioned.get("phone_number")
        row.twilio_phone_sid = provisioned.get("phone_sid")

    if row.credits_balance == 0 and row.free_credits == 0:
        row.credits_balance = pricing.sms_starting_credits
        row.free_credits = pricing.sms_starting_credits

    db.add(row)
    db.commit()
    db.refresh(row)

    has_ledger, ledger_balance = _calculate_credit_balance(db, user.id)
    credits_balance = ledger_balance if has_ledger else (row.credits_balance or 0)

    return SmsSettingsOut(
        enabled=bool(row.enabled),
        twilio_phone_number=row.twilio_phone_number,
        twilio_phone_sid=row.twilio_phone_sid,
        forwarding_enabled=bool(row.forwarding_enabled),
        forward_to_phone=row.forward_to_phone,
        bundle_size=row.bundle_size or 1000,
        credits_balance=credits_balance,
        free_credits=row.free_credits or 0,
        terms_accepted_at=row.terms_accepted_at,
        terms_version=row.terms_version,
    )

@router.post("/settings", response_model=SmsSettingsOut)
def update_sms_settings(
    payload: SmsSettingsIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _ensure_sms_settings(db, user.id)

    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)

    if payload.forwarding_enabled is not None:
        row.forwarding_enabled = bool(payload.forwarding_enabled)

    if payload.forward_to_phone is not None:
        row.forward_to_phone = payload.forward_to_phone.strip() or None

    if payload.bundle_size is not None:
        row.bundle_size = int(payload.bundle_size)

    if payload.credits_balance is not None:
        row.credits_balance = int(payload.credits_balance)

    if payload.free_credits is not None:
        row.free_credits = int(payload.free_credits)

    if payload.twilio_phone_number is not None:
        row.twilio_phone_number = payload.twilio_phone_number.strip() or None

    if payload.twilio_phone_sid is not None:
        row.twilio_phone_sid = payload.twilio_phone_sid.strip() or None

    db.add(row)
    db.commit()
    db.refresh(row)

    has_ledger, ledger_balance = _calculate_credit_balance(db, user.id)
    credits_balance = ledger_balance if has_ledger else (row.credits_balance or 0)

    return SmsSettingsOut(
        enabled=bool(row.enabled),
        twilio_phone_number=row.twilio_phone_number,
        twilio_phone_sid=row.twilio_phone_sid,
        forwarding_enabled=bool(row.forwarding_enabled),
        forward_to_phone=row.forward_to_phone,
        bundle_size=row.bundle_size or 1000,
        credits_balance=credits_balance,
        free_credits=row.free_credits or 0,
        terms_accepted_at=row.terms_accepted_at,
        terms_version=row.terms_version,
    )
