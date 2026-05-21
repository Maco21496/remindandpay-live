# api/app/routers/sms_settings.py
from datetime import datetime
import calendar
import os
from typing import Optional, List
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import requests

from ..shared import APIRouter
from ..database import get_db
from ..models import AccountBillingTransaction, AccountSmsSettings, SmsCreditLedger, SmsPricingSettings, EmailOutbox, User, Customer
from ..crypto_secrets import encrypt_secret, decrypt_secret
from .auth import require_user
from ..services.billing_trial import assert_billing_allows_sending
router = APIRouter(prefix="/api/sms", tags=["sms_settings"])
credits_router = APIRouter(prefix="/api/credits", tags=["credits"])

def _add_months(anchor: datetime, months: int = 1) -> datetime:
    month_index = (anchor.month - 1) + months
    year = anchor.year + (month_index // 12)
    month = (month_index % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(anchor.day, last_day)
    return anchor.replace(year=year, month=month, day=day)

def _effective_credit_balance(db: Session, row: AccountSmsSettings) -> int:
    has_ledger, ledger_balance = _calculate_credit_balance(db, row)
    if has_ledger:
        return ledger_balance
    return int(row.credits_balance or 0)

def _render_template(template: str, context: dict) -> str:
    out = template or ""
    for key, value in context.items():
        out = out.replace(f"{{{{{key}}}}}", str(value if value is not None else ""))
    return out

def _enqueue_app_notification(
    db: Session,
    *,
    user: User,
    event_key: str,
    context: dict,
    dedupe_key: Optional[str],
) -> bool:
    template_row = db.execute(
        text(
            """
            SELECT event_key, enabled, subject_template, body_template, from_email, from_name, cooldown_minutes
            FROM app_notification_templates
            WHERE event_key = :event_key AND channel = 'email'
            LIMIT 1
            """
        ),
        {"event_key": event_key},
    ).mappings().first()
    if not template_row or int(template_row.get("enabled") or 0) != 1:
        return False

    log_id = None
    if dedupe_key:
        inserted = db.execute(
            text(
                """
                INSERT INTO app_notification_log (user_id, event_key, channel, dedupe_key, status, detail)
                VALUES (:user_id, :event_key, 'email', :dedupe_key, 'queued', JSON_OBJECT('source','sms_scheduler'))
                ON DUPLICATE KEY UPDATE id = id
                """
            ),
            {"user_id": user.id, "event_key": event_key, "dedupe_key": dedupe_key},
        )
        if (inserted.rowcount or 0) == 0:
            return False
        log_row = db.execute(
            text(
                """
                SELECT id FROM app_notification_log
                WHERE dedupe_key = :dedupe_key
                LIMIT 1
                """
            ),
            {"dedupe_key": dedupe_key},
        ).mappings().first()
        if log_row:
            log_id = log_row["id"]

    subject = _render_template(template_row["subject_template"], context)
    body = _render_template(template_row["body_template"], context)
    from_email = (template_row.get("from_email") or os.getenv("NOTIFICATIONS_EMAIL", "")).strip() or None
    from_name = (template_row.get("from_name") or os.getenv("NOTIFICATIONS_FROM_NAME", "Remind & Pay")).strip()

    db.add(
        EmailOutbox(
            user_id=user.id,
            customer_id=None,
            invoice_id=None,
            channel="email",
            template=f"app_notification:{event_key}",
            server_scope="default_server",
            to_email=user.email,
            subject=subject,
            body=body,
            payload_json={
                "app_notification": True,
                "event_key": event_key,
                "from_email": from_email,
                "from_name": from_name,
                "notification_log_id": log_id,
            },
            status="queued",
            next_attempt_at=datetime.utcnow(),
        )
    )
    return True

def _load_notification_triggers(db: Session) -> dict[str, dict]:
    rows = db.execute(
        text(
            """
            SELECT event_key, enabled, trigger_type, threshold_value, threshold_unit
            FROM app_notification_triggers
            """
        )
    ).mappings().all()
    out: dict[str, dict] = {}
    for r in rows:
        out[str(r["event_key"])] = {
            "enabled": int(r.get("enabled") or 0) == 1,
            "trigger_type": (r.get("trigger_type") or "").strip().lower(),
            "threshold_value": float(r.get("threshold_value") or 0),
            "threshold_unit": (r.get("threshold_unit") or "").strip().lower(),
        }
    return out

def _release_twilio_number(row: AccountSmsSettings) -> tuple[bool, str]:
    if not row.twilio_subaccount_sid or not row.twilio_phone_sid:
        return True, "no_number_to_release"
    try:
        master_sid, api_key_sid, api_key_secret, master_auth_token = _twilio_credentials()
    except Exception as e:
        return False, f"credentials_error:{e}"
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, api_key_sid, api_key_secret)
    release_url = f"https://api.twilio.com/2010-04-01/Accounts/{row.twilio_subaccount_sid}/IncomingPhoneNumbers/{row.twilio_phone_sid}.json"
    r = _twilio_request_with_fallback(
        "DELETE",
        release_url,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
        timeout=20,
    )
    if r.ok:
        return True, "released"
    return False, f"twilio_delete_failed:{r.status_code}:{(r.text or '')[:200]}"

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
    next_number_charge_at: Optional[datetime] = None
    past_due_since: Optional[datetime] = None
    released_at: Optional[datetime] = None
    release_reason: Optional[str] = None
    sms_monthly_number_cost: Optional[int] = None

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


class LedgerEntryOut(BaseModel):
    id: int
    created_at: datetime
    entry_type: str
    amount: int
    reason: str
    reference_id: Optional[str] = None
    details: Optional[dict] = None
    balance_after: int
    category: Optional[str] = None
    movement: Optional[str] = None
    description: Optional[str] = None
    to_display: Optional[str] = None
    segments_display: Optional[str] = None


class LedgerOut(BaseModel):
    balance: int
    entries: List[LedgerEntryOut]


def _ensure_sms_settings(db: Session, user_id: int) -> AccountSmsSettings:
    row = (
        db.query(AccountSmsSettings)
        .filter(AccountSmsSettings.user_id == user_id)
        .first()
    )
    if row:
        return row
    row = AccountSmsSettings(
        user_id=user_id,
        chasing_delivery_mode="email",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def _calculate_credit_balance(db: Session, row: AccountSmsSettings) -> tuple[bool, int]:
    user_id = row.user_id
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

    has_credit = (
        db.query(SmsCreditLedger.id)
        .filter(
            SmsCreditLedger.user_id == user_id,
            SmsCreditLedger.entry_type == "credit",
        )
        .first()
        is not None
    )
    base_balance = 0
    if not has_credit:
        base_balance = int(row.credits_balance or 0)

    return True, int(total or 0) + base_balance

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

def _twilio_credentials() -> tuple[str, str, str, str]:
    master_sid = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
    api_key_sid = (os.getenv("TWILIO_API_KEY_SID", "") or "").strip()
    api_key_secret = (os.getenv("TWILIO_API_KEY_SECRET", "") or "").strip()
    master_auth_token = (os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()
    if not master_sid or not api_key_sid or not api_key_secret:
        raise HTTPException(status_code=400, detail="Twilio API key credentials not configured.")
    return master_sid, api_key_sid, api_key_secret, master_auth_token

def _fetch_subaccount_auth_token(subaccount_sid: str, master_sid: str, master_auth_token: str) -> Optional[str]:
    if not master_auth_token:
        return None
    subaccount_url = f"https://api.twilio.com/2010-04-01/Accounts/{subaccount_sid}.json"
    r_sub = requests.get(
        subaccount_url,
        auth=_twilio_auth_headers(master_sid, master_auth_token),
        timeout=20,
    )
    if not r_sub.ok:
        return None
    return (r_sub.json() or {}).get("auth_token")

def _subaccount_primary_auth(master_sid: str, master_auth_token: str, api_key_sid: str, api_key_secret: str) -> tuple[str, str]:
    return _twilio_auth_headers(api_key_sid, api_key_secret)

def _twilio_request_with_fallback(
    method: str,
    url: str,
    *,
    primary_auth: tuple[str, str],
    fallback_auth: Optional[tuple[str, str]] = None,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
    timeout: int = 20,
):
    response = requests.request(
        method,
        url,
        params=params,
        data=data,
        auth=primary_auth,
        timeout=timeout,
    )
    if response.status_code == 401 and fallback_auth and fallback_auth != primary_auth:
        response = requests.request(
            method,
            url,
            params=params,
            data=data,
            auth=fallback_auth,
            timeout=timeout,
        )
    return response

def _twilio_friendly_name(user_email: str) -> str:
    return f"RemindPay {user_email or 'Account'}"

def _find_active_subaccount_by_name(
    friendly_name: str,
    api_key_sid: str,
    api_key_secret: str,
    master_sid: str,
    master_auth_token: str,
) -> Optional[str]:
    list_url = "https://api.twilio.com/2010-04-01/Accounts.json"
    params = {
        "FriendlyName": friendly_name,
        "Status": "active",
        "PageSize": 20,
    }
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, api_key_sid, api_key_secret)
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    r_list = _twilio_request_with_fallback(
        "GET",
        list_url,
        params=params,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    if not r_list.ok:
        return None
    accounts = (r_list.json() or {}).get("accounts") or []
    for account in accounts:
        if (account.get("friendly_name") or "") == friendly_name and (account.get("status") or "").lower() == "active":
            sid = (account.get("sid") or "").strip()
            if sid:
                return sid
    return None

def _is_subaccount_active(
    account_sid: str,
    api_key_sid: str,
    api_key_secret: str,
    master_sid: str,
    master_auth_token: str,
) -> bool:
    if not account_sid:
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json"
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, api_key_sid, api_key_secret)
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    r_account = _twilio_request_with_fallback(
        "GET",
        url,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    if not r_account.ok:
        return False
    status_value = ((r_account.json() or {}).get("status") or "").lower()
    return status_value == "active"

def _find_existing_bundle_sid(
    *,
    account_sid: str,
    friendly_name: str,
    api_key_sid: str,
    api_key_secret: str,
    master_sid: str,
    master_auth_token: str,
) -> Optional[str]:
    bundle_url = "https://numbers.twilio.com/v2/RegulatoryCompliance/Bundles"
    params = {
        "AccountSid": account_sid,
        "FriendlyName": friendly_name,
        "PageSize": 20,
    }
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, api_key_sid, api_key_secret)
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    r_bundle = _twilio_request_with_fallback(
        "GET",
        bundle_url,
        params=params,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    if not r_bundle.ok:
        return None
    bundles = (r_bundle.json() or {}).get("bundles") or []
    for bundle in bundles:
        sid = (bundle.get("sid") or bundle.get("bundle_sid") or bundle.get("bundleSid") or "").strip()
        status_value = (bundle.get("status") or "").lower()
        if sid and status_value not in {"rejected", "draft"}:
            return sid
    return None

def _configure_incoming_number(
    *,
    account_sid: str,
    phone_sid: str,
    webhook_base: str,
    api_key_sid: str,
    api_key_secret: str,
    bundle_sid: Optional[str],
    master_sid: str,
    master_auth_token: str,
) -> dict:
    inbound_url = f"{webhook_base.rstrip('/')}/api/sms/webhooks/inbound"
    status_url = f"{webhook_base.rstrip('/')}/api/sms/webhooks/status"
    update_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers/{phone_sid}.json"
    update_payload = {
        "SmsUrl": inbound_url,
        "SmsMethod": "POST",
        "StatusCallback": status_url,
        "StatusCallbackMethod": "POST",
    }
    if bundle_sid:
        update_payload["BundleSid"] = bundle_sid
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, api_key_sid, api_key_secret)
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    r_update = _twilio_request_with_fallback(
        "POST",
        update_url,
        data=update_payload,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    r_update.raise_for_status()
    data = r_update.json()
    return {
        "phone_number": data.get("phone_number"),
        "phone_sid": data.get("sid") or phone_sid,
    }

def _find_existing_phone_number(
    *,
    account_sid: str,
    webhook_base: str,
    api_key_sid: str,
    api_key_secret: str,
    bundle_sid: Optional[str],
    master_sid: str,
    master_auth_token: str,
) -> Optional[dict]:
    list_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    params = {"PageSize": 20}
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, api_key_sid, api_key_secret)
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    r_list = _twilio_request_with_fallback(
        "GET",
        list_url,
        params=params,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    if not r_list.ok:
        return None
    numbers = (r_list.json() or {}).get("incoming_phone_numbers") or []
    for number in numbers:
        if not number.get("sms_enabled"):
            continue
        sid = (number.get("sid") or "").strip()
        if not sid:
            continue
        configured = _configure_incoming_number(
            account_sid=account_sid,
            phone_sid=sid,
            webhook_base=webhook_base,
            api_key_sid=api_key_sid,
            api_key_secret=api_key_secret,
            bundle_sid=bundle_sid,
            master_sid=master_sid,
            master_auth_token=master_auth_token,
        )
        configured["phone_number"] = configured.get("phone_number") or number.get("phone_number")
        return configured
    return None

def _clone_twilio_bundle(
    *,
    parent_bundle_sid: str,
    target_account_sid: str,
    api_key_sid: str,
    api_key_secret: str,
    friendly_name: str,
) -> str:
    clone_url = f"https://numbers.twilio.com/v2/RegulatoryCompliance/Bundles/{parent_bundle_sid}/Clones"
    clone_payload = {
        "TargetAccountSid": target_account_sid,
        "FriendlyName": friendly_name,
        "MoveToDraft": "false",
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
    return bundle_sid

def _provision_twilio_number(
    *,
    country: str,
    webhook_base: str,
    account_sid: str,
    auth_sid: str,
    auth_secret: str,
    bundle_sid: Optional[str],
    master_sid: str,
    master_auth_token: str,
) -> dict:
    available_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/AvailablePhoneNumbers/"
        f"{country}/Mobile.json"
    )
    available_params = {
        "SmsEnabled": "true",
        "PageSize": 1,
    }
    primary_auth = _subaccount_primary_auth(master_sid, master_auth_token, auth_sid, auth_secret)
    fallback_auth = _twilio_auth_headers(master_sid, master_auth_token) if master_auth_token else None
    r_available = _twilio_request_with_fallback(
        "GET",
        available_url,
        params=available_params,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    if r_available.status_code == 401:
        raise HTTPException(
            status_code=400,
            detail="Twilio subaccount authorization failed. Set TWILIO_AUTH_TOKEN or ensure the API key can access subaccounts.",
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
    r_purchase = _twilio_request_with_fallback(
        "POST",
        purchase_url,
        data=purchase_payload,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
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
    existing_subaccount_sid: Optional[str] = None,
    existing_bundle_sid: Optional[str] = None,
    existing_phone_sid: Optional[str] = None,
    existing_phone_number: Optional[str] = None,
) -> dict:
    master_sid, api_key_sid, api_key_secret, master_auth_token = _twilio_credentials()
    if not parent_bundle_sid:
        raise HTTPException(status_code=400, detail="TWILIO_PARENT_BUNDLE_SID is not configured.")

    friendly_name = _twilio_friendly_name(user_email)
    bundle_friendly_name = f"{friendly_name} bundle"

    sub_sid = (existing_subaccount_sid or "").strip()
    sub_token: Optional[str] = None
    if sub_sid and not _is_subaccount_active(sub_sid, api_key_sid, api_key_secret, master_sid, master_auth_token):
        sub_sid = ""
    if not sub_sid:
        sub_sid = _find_active_subaccount_by_name(
            friendly_name,
            api_key_sid,
            api_key_secret,
            master_sid,
            master_auth_token,
        ) or ""

    if not sub_sid:
        create_url = "https://api.twilio.com/2010-04-01/Accounts.json"
        payload = {"FriendlyName": friendly_name}
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
        if not sub_sid:
            raise HTTPException(status_code=502, detail="Twilio did not return a subaccount SID.")

    if not sub_token:
        sub_token = _fetch_subaccount_auth_token(sub_sid, master_sid, master_auth_token)

    bundle_sid = (existing_bundle_sid or "").strip()
    if not bundle_sid:
        bundle_sid = _find_existing_bundle_sid(
            account_sid=sub_sid,
            friendly_name=bundle_friendly_name,
            api_key_sid=api_key_sid,
            api_key_secret=api_key_secret,
            master_sid=master_sid,
            master_auth_token=master_auth_token,
        ) or ""

    if not bundle_sid:
        bundle_sid = _clone_twilio_bundle(
            parent_bundle_sid=parent_bundle_sid,
            target_account_sid=sub_sid,
            api_key_sid=api_key_sid,
            api_key_secret=api_key_secret,
            friendly_name=bundle_friendly_name,
        )

    provisioned: dict = {}
    has_existing_phone = bool((existing_phone_sid or "").strip() and (existing_phone_number or "").strip())
    if has_existing_phone:
        provisioned = _configure_incoming_number(
            account_sid=sub_sid,
            phone_sid=(existing_phone_sid or "").strip(),
            webhook_base=webhook_base,
            api_key_sid=api_key_sid,
            api_key_secret=api_key_secret,
            bundle_sid=bundle_sid,
            master_sid=master_sid,
            master_auth_token=master_auth_token,
        )
        provisioned["phone_number"] = provisioned.get("phone_number") or (existing_phone_number or "").strip()
    else:
        provisioned = _find_existing_phone_number(
            account_sid=sub_sid,
            webhook_base=webhook_base,
            api_key_sid=api_key_sid,
            api_key_secret=api_key_secret,
            bundle_sid=bundle_sid,
            master_sid=master_sid,
            master_auth_token=master_auth_token,
        ) or {}

    if not provisioned.get("phone_sid"):
        provisioned = _provision_twilio_number(
            country=country,
            webhook_base=webhook_base,
            account_sid=sub_sid,
            auth_sid=api_key_sid,
            auth_secret=api_key_secret,
            bundle_sid=bundle_sid,
            master_sid=master_sid,
            master_auth_token=master_auth_token,
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


@router.get("/ledger", response_model=LedgerOut)
def get_sms_ledger(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    row = _ensure_sms_settings(db, user.id)
    has_ledger, ledger_balance = _calculate_credit_balance(db, row)
    balance = ledger_balance if has_ledger else (row.credits_balance or 0)

    entries = (
        db.query(SmsCreditLedger)
        .filter(SmsCreditLedger.user_id == user.id)
        .order_by(SmsCreditLedger.created_at.desc(), SmsCreditLedger.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    customer_ids = {
        int((e.details or {}).get("customer_id"))
        for e in entries
        if (e.details or {}).get("customer_id") is not None
    }
    customers_by_id = {}
    if customer_ids:
        customer_rows = db.query(Customer.id, Customer.name).filter(Customer.id.in_(customer_ids)).all()
        customers_by_id = {int(cid): (name or "").strip() for cid, name in customer_rows}

    running = balance
    items: List[LedgerEntryOut] = []
    for entry in entries:
        details = entry.details or {}
        reason = entry.reason or ""
        category = "Credit"
        description = reason.replace("_", " ").title()
        to_display = details.get("to") or "-"
        segments_display = str(details.get("segments")) if details.get("segments") is not None else "-"
        if reason == "sms_number_monthly":
            category = "Renewal"
            description = "Monthly number renewal"
            to_display = row.twilio_phone_number or "-"
            segments_display = "-"
        elif reason == "sms_send":
            category = "SMS sent"
            customer_name = customers_by_id.get(int(details.get("customer_id"))) if details.get("customer_id") is not None else ""
            target = customer_name or details.get("to") or "recipient"
            description = f"SMS to {target}"
            to_display = details.get("to") or "-"
            segments_display = str(details.get("segments")) if details.get("segments") is not None else "-"
        elif reason in ("billing_transaction_topup", "stripe_topup"):
            category = "Credit top-up"
            description = "Credit top-up"
            to_display = "-"
            segments_display = "-"
        elif reason in ("billing_transaction_refund_reversal", "stripe_refund_reversal"):
            category = "Refund"
            description = "Top-up refund reversal"
            to_display = "-"
            segments_display = "-"
        elif reason == "manual_admin_topup":
            category = "Manual adjustment"
            description = "Manual credit adjustment"
            to_display = "-"
            segments_display = "-"
        elif reason == "starter_pack":
            category = "Starter credits"
            description = "Starter credits"
            to_display = "-"
            segments_display = "-"
        items.append(
            LedgerEntryOut(
                id=entry.id,
                created_at=entry.created_at,
                entry_type=entry.entry_type,
                amount=entry.amount,
                reason=entry.reason,
                reference_id=entry.reference_id,
                details=details,
                balance_after=running,
                category=category,
                movement=("Credit" if entry.entry_type == "credit" else "Debit"),
                description=description,
                to_display=to_display,
                segments_display=segments_display,
            )
        )
        if entry.entry_type == "credit":
            running -= entry.amount
        else:
            running += entry.amount

    return LedgerOut(balance=balance, entries=items)

@router.get("/settings", response_model=SmsSettingsOut)
def get_sms_settings(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _ensure_sms_settings(db, user.id)
    has_ledger, ledger_balance = _calculate_credit_balance(db, row)
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
        next_number_charge_at=row.next_number_charge_at,
        past_due_since=row.past_due_since,
        released_at=row.released_at,
        release_reason=row.release_reason,
        sms_monthly_number_cost=int(pricing.sms_monthly_number_cost or 0),
    )

@router.post("/enable", response_model=SmsSettingsOut)
def enable_sms(
    payload: SmsTermsIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    try:
        assert_billing_allows_sending(db, user)
    except ValueError as e:
        raise HTTPException(status_code=402, detail=str(e))

    if not payload.accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Terms acceptance required")

    row = _ensure_sms_settings(db, user.id)
    first_enable = row.terms_accepted_at is None
    first_starter_credits = (row.starter_credits_granted_at is None) and first_enable
    pricing = _ensure_pricing(db)
    snapshot = payload.pricing_snapshot or _build_pricing_snapshot(pricing)
    webhook_base = (os.getenv("TWILIO_WEBHOOK_BASE_URL", "") or "").strip()
    country = (payload.country or os.getenv("TWILIO_DEFAULT_COUNTRY", "GB") or "GB").upper()
    if not webhook_base:
        raise HTTPException(status_code=400, detail="TWILIO_WEBHOOK_BASE_URL is not configured.")

    monthly_cost = int(pricing.sms_monthly_number_cost or 0)
    if not first_starter_credits and monthly_cost > 0:
        current_balance = _effective_credit_balance(db, row)
        if current_balance < monthly_cost:
            raise HTTPException(
                status_code=400,
                detail=f"You need at least {monthly_cost} SMS credits to enable SMS. Please top up first.",
            )

    now = datetime.utcnow()
    row.enabled = True
    row.terms_accepted_at = now
    row.terms_version = (payload.terms_version or "v1")[:32]
    row.terms_accepted_ip = request.client.host if request.client else None
    row.accepted_pricing_snapshot = snapshot

    parent_bundle_sid = (os.getenv("TWILIO_PARENT_BUNDLE_SID", "") or "").strip()

    needs_subaccount = not row.twilio_subaccount_sid
    needs_bundle = not row.twilio_bundle_sid
    needs_phone = not row.twilio_phone_sid or not row.twilio_phone_number

    if needs_subaccount or needs_bundle or needs_phone:
        provisioned = _ensure_twilio_subaccount(
            user_email=user.email or "",
            webhook_base=webhook_base,
            country=country,
            parent_bundle_sid=parent_bundle_sid,
            existing_subaccount_sid=row.twilio_subaccount_sid,
            existing_bundle_sid=row.twilio_bundle_sid,
            existing_phone_sid=row.twilio_phone_sid if row.twilio_phone_sid and row.twilio_phone_number else None,
            existing_phone_number=row.twilio_phone_number if row.twilio_phone_sid and row.twilio_phone_number else None,
        )
        if needs_subaccount:
            row.twilio_subaccount_sid = provisioned["subaccount_sid"]
        if provisioned.get("auth_token"):
            row.twilio_auth_token_enc = encrypt_secret(provisioned["auth_token"])
        if needs_bundle and provisioned.get("bundle_sid"):
            row.twilio_bundle_sid = provisioned.get("bundle_sid")
        if needs_phone:
            row.twilio_phone_number = provisioned.get("phone_number") or row.twilio_phone_number
            row.twilio_phone_sid = provisioned.get("phone_sid") or row.twilio_phone_sid
            if not row.twilio_phone_sid or not row.twilio_phone_number:
                raise HTTPException(status_code=502, detail="Twilio did not return a provisioned phone number.")

    if row.sms_enabled_at is None:
        row.sms_enabled_at = now

    if row.next_number_charge_at is None:
        row.next_number_charge_at = _add_months(now, 1)

    if first_enable:
        row.past_due_since = None

    if first_starter_credits and row.credits_balance == 0 and row.free_credits == 0:
        row.credits_balance = pricing.sms_starting_credits
        row.free_credits = pricing.sms_starting_credits

    if first_starter_credits:
        starter_credits = int(pricing.sms_starting_credits or 0)
        if starter_credits > 0:
            db.add(
                SmsCreditLedger(
                    user_id=user.id,
                    entry_type="credit",
                    amount=starter_credits,
                    reason="starter_pack",
                    reference_id=f"sms_enable_starter:{row.id}",
                    details={
                        "source": "sms_enable",
                        "note": "Starter credits",
                    },
                )
            )
        row.starter_credits_granted_at = now
        if monthly_cost > 0:
            db.add(
                SmsCreditLedger(
                    user_id=user.id,
                    entry_type="debit",
                    amount=monthly_cost,
                    reason="sms_number_monthly",
                    reference_id=f"sms_enable_monthly:{row.id}",
                    details={
                        "source": "sms_enable",
                        "note": "Initial monthly number charge",
                    },
                )
            )

    db.add(row)
    db.commit()
    db.refresh(row)

    has_ledger, ledger_balance = _calculate_credit_balance(db, row)
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

@credits_router.post("/number-renewals/enqueue-due")
def enqueue_due_number_renewals(db: Session = Depends(get_db)):
    """Process due monthly phone-number renewal credit debits and past-due handling."""
    now = datetime.utcnow()
    pricing = _ensure_pricing(db)
    trigger_rules = _load_notification_triggers(db)
    monthly_cost = int(pricing.sms_monthly_number_cost or 0)
    rows = (
        db.query(AccountSmsSettings)
        .filter(
            AccountSmsSettings.enabled == True,  # noqa: E712
            AccountSmsSettings.twilio_phone_number.isnot(None),
            AccountSmsSettings.next_number_charge_at.isnot(None),
            AccountSmsSettings.next_number_charge_at <= now,
        )
        .order_by(AccountSmsSettings.next_number_charge_at.asc())
        .all()
    )

    processed = 0
    charged = 0
    past_due = 0
    skipped = 0

    for row in rows:
        due_at = row.next_number_charge_at or now
        cycle = due_at.strftime("%Y-%m")
        balance = _effective_credit_balance(db, row)
        user = db.query(User).filter(User.id == row.user_id).first()
        if not user:
            continue

        if monthly_cost <= 0:
            row.next_number_charge_at = _add_months(due_at, 1)
            row.past_due_since = None
            db.add(row)
            db.commit()
            processed += 1
            skipped += 1
            continue

        if balance >= monthly_cost:
            reference_id = f"sms_number_monthly:settings:{row.id}:{cycle}"
            already_charged = False
            db.add(
                SmsCreditLedger(
                    user_id=row.user_id,
                    entry_type="debit",
                    amount=monthly_cost,
                    reason="sms_number_monthly",
                    reference_id=reference_id,
                    details={
                        "source": "number_renewal_scheduler",
                        "cycle": cycle,
                    },
                )
            )
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                already_charged = True

            row.next_number_charge_at = _add_months(due_at, 1)
            row.past_due_since = None
            db.add(row)
            db.commit()
            processed += 1
            if already_charged:
                skipped += 1
            else:
                charged += 1
                low_trigger = trigger_rules.get("sms_low_balance", {})
                low_enabled = low_trigger.get("enabled", True)
                low_type = low_trigger.get("trigger_type") or "low_balance_multiplier"
                low_value = float(low_trigger.get("threshold_value") or 2)
                if low_type == "low_balance_fixed":
                    low_balance_threshold = int(low_value)
                else:
                    low_balance_threshold = int(monthly_cost * (low_value if low_value > 0 else 2))
                new_balance = _effective_credit_balance(db, row)
                if low_enabled and new_balance < low_balance_threshold:
                    _enqueue_app_notification(
                        db,
                        user=user,
                        event_key="sms_low_balance",
                        context={
                            "user_name": user.email,
                            "balance": new_balance,
                            "monthly_cost": monthly_cost,
                        },
                        dedupe_key=f"sms_low_balance:{row.id}:{cycle}",
                    )
                    db.commit()
            continue

        if row.past_due_since is None:
            row.past_due_since = now
            _enqueue_app_notification(
                db,
                user=user,
                event_key="sms_number_past_due",
                context={
                    "user_name": user.email,
                    "balance": balance,
                    "monthly_cost": monthly_cost,
                },
                dedupe_key=f"sms_number_past_due:{row.id}:{cycle}",
            )
        else:
            suspend_after_days = int(pricing.sms_suspend_after_days or 0)
            if suspend_after_days > 0:
                due_release_at = row.past_due_since + timedelta(days=suspend_after_days)
                days_left = max(0, (due_release_at.date() - now.date()).days)
                warning_trigger = trigger_rules.get("sms_release_warning", {})
                warning_enabled = warning_trigger.get("enabled", True)
                warning_type = warning_trigger.get("trigger_type") or "release_warning_hours"
                warning_value = float(warning_trigger.get("threshold_value") or 72)
                if warning_type == "release_warning_hours":
                    warning_days = max(1, int(warning_value // 24))
                else:
                    warning_days = max(1, int(warning_value or 3))
                if warning_enabled and days_left <= warning_days:
                    _enqueue_app_notification(
                        db,
                        user=user,
                        event_key="sms_release_warning",
                        context={
                            "user_name": user.email,
                            "days_left": days_left,
                            "balance": balance,
                        },
                        dedupe_key=f"sms_release_warning:{row.id}:{due_release_at.date().isoformat()}:{days_left}",
                    )
                if now >= due_release_at and not bool(getattr(row, "do_not_release_number", False)):
                    ok, release_msg = _release_twilio_number(row)
                    if ok:
                        row.enabled = False
                        row.twilio_phone_number = None
                        row.twilio_phone_sid = None
                        row.next_number_charge_at = None
                        row.released_at = now
                        row.release_reason = "insufficient_credits"
                        row.past_due_since = None
                        _enqueue_app_notification(
                            db,
                            user=user,
                            event_key="sms_number_released",
                            context={
                                "user_name": user.email,
                            },
                            dedupe_key=f"sms_number_released:{row.id}:{now.date().isoformat()}",
                        )
                    else:
                        row.release_reason = release_msg
        db.add(row)
        db.commit()
        processed += 1
        past_due += 1

    return {
        "ok": True,
        "processed": processed,
        "charged": charged,
        "past_due": past_due,
        "skipped": skipped,
    }

@router.post("/settings", response_model=SmsSettingsOut)
def update_sms_settings(
    payload: SmsSettingsIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _ensure_sms_settings(db, user.id)
    pricing = _ensure_pricing(db)

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

    has_ledger, ledger_balance = _calculate_credit_balance(db, row)
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
        next_number_charge_at=row.next_number_charge_at,
        past_due_since=row.past_due_since,
        released_at=row.released_at,
        release_reason=row.release_reason,
        sms_monthly_number_cost=int(pricing.sms_monthly_number_cost or 0),
    )
