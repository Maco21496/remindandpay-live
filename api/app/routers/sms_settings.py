# api/app/routers/sms_settings.py
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from ..models import AccountSmsSettings, SmsCreditLedger, SmsPricingSettings
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

    row.enabled = True
    row.terms_accepted_at = datetime.utcnow()
    row.terms_version = (payload.terms_version or "v1")[:32]
    row.terms_accepted_ip = request.client.host if request.client else None
    row.accepted_pricing_snapshot = snapshot

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
