# api/app/routers/sms_settings.py
from typing import Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from ..models import AccountSmsSettings, SmsCreditLedger
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

class SmsSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    twilio_phone_number: Optional[str] = None
    twilio_phone_sid: Optional[str] = None
    forwarding_enabled: Optional[bool] = None
    forward_to_phone: Optional[str] = None
    bundle_size: Optional[int] = Field(None, ge=100, le=100000)
    credits_balance: Optional[int] = Field(None, ge=0)
    free_credits: Optional[int] = Field(None, ge=0)


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
    )
