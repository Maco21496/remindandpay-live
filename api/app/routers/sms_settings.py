# api/app/routers/sms_settings.py
from typing import Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..shared import APIRouter, HTTPException
from ..database import get_db
from ..models import AccountSmsSettings
from .auth import require_user
from ..crypto_secrets import encrypt_secret

router = APIRouter(prefix="/api/sms", tags=["sms_settings"])

DELIVERY_MODES = {"email", "sms", "both"}

class SmsSettingsOut(BaseModel):
    enabled: bool
    delivery_mode: str
    twilio_phone_number: Optional[str] = None
    twilio_phone_sid: Optional[str] = None
    forwarding_enabled: bool
    forward_to_phone: Optional[str] = None
    bundle_size: int
    credits_balance: int
    free_credits: int
    has_account_sid: bool
    has_auth_token: bool

class SmsSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    delivery_mode: Optional[str] = Field(None, description="email|sms|both")
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
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

@router.get("/settings", response_model=SmsSettingsOut)
def get_sms_settings(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _ensure_sms_settings(db, user.id)
    return SmsSettingsOut(
        enabled=bool(row.enabled),
        delivery_mode=row.delivery_mode or "email",
        twilio_phone_number=row.twilio_phone_number,
        twilio_phone_sid=row.twilio_phone_sid,
        forwarding_enabled=bool(row.forwarding_enabled),
        forward_to_phone=row.forward_to_phone,
        bundle_size=row.bundle_size or 1000,
        credits_balance=row.credits_balance or 0,
        free_credits=row.free_credits or 0,
        has_account_sid=bool(row.twilio_account_sid_enc),
        has_auth_token=bool(row.twilio_auth_token_enc),
    )

@router.post("/settings", response_model=SmsSettingsOut)
def update_sms_settings(
    payload: SmsSettingsIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _ensure_sms_settings(db, user.id)

    if payload.delivery_mode is not None:
        mode = payload.delivery_mode.lower().strip()
        if mode not in DELIVERY_MODES:
            raise HTTPException(400, "delivery_mode must be email, sms, or both")
        row.delivery_mode = mode

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

    if payload.twilio_account_sid is not None:
        if payload.twilio_account_sid.strip():
            row.twilio_account_sid_enc = encrypt_secret(payload.twilio_account_sid.strip())
        else:
            row.twilio_account_sid_enc = None

    if payload.twilio_auth_token is not None:
        if payload.twilio_auth_token.strip():
            row.twilio_auth_token_enc = encrypt_secret(payload.twilio_auth_token.strip())
        else:
            row.twilio_auth_token_enc = None

    if payload.twilio_phone_number is not None:
        row.twilio_phone_number = payload.twilio_phone_number.strip() or None

    if payload.twilio_phone_sid is not None:
        row.twilio_phone_sid = payload.twilio_phone_sid.strip() or None

    db.add(row)
    db.commit()
    db.refresh(row)

    return SmsSettingsOut(
        enabled=bool(row.enabled),
        delivery_mode=row.delivery_mode or "email",
        twilio_phone_number=row.twilio_phone_number,
        twilio_phone_sid=row.twilio_phone_sid,
        forwarding_enabled=bool(row.forwarding_enabled),
        forward_to_phone=row.forward_to_phone,
        bundle_size=row.bundle_size or 1000,
        credits_balance=row.credits_balance or 0,
        free_credits=row.free_credits or 0,
        has_account_sid=bool(row.twilio_account_sid_enc),
        has_auth_token=bool(row.twilio_auth_token_enc),
    )
