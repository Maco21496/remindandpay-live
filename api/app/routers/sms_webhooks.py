# api/app/routers/sms_webhooks.py
import base64
import hashlib
import hmac
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..crypto_secrets import decrypt_secret
from ..database import get_db
from ..models import AccountSmsSettings

router = APIRouter(prefix="/api/sms/webhooks", tags=["sms-webhooks"])


def _normalize_params(form: dict) -> dict:
    return {k: v if not isinstance(v, list) else (v[0] if v else "") for k, v in form.items()}


def _build_twilio_signature(url: str, params: dict, auth_token: str) -> str:
    base = url
    for key in sorted(params.keys()):
        base += f"{key}{params[key]}"
    digest = hmac.new(auth_token.encode("utf-8"), base.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def _validate_twilio_signature(request: Request, params: dict, auth_token: str) -> None:
    if (os.getenv("TWILIO_VALIDATE_SIGNATURE", "") or "").strip().lower() not in {"1", "true", "yes"}:
        return
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Twilio signature")
    expected = _build_twilio_signature(str(request.url), params, auth_token)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature")


def _lookup_sms_settings(db: Session, account_sid: Optional[str], to_number: Optional[str]) -> Optional[AccountSmsSettings]:
    if account_sid:
        row = (
            db.query(AccountSmsSettings)
            .filter(AccountSmsSettings.twilio_subaccount_sid == account_sid)
            .first()
        )
        if row:
            return row
    if to_number:
        return (
            db.query(AccountSmsSettings)
            .filter(AccountSmsSettings.twilio_phone_number == to_number)
            .first()
        )
    return None


def _update_outbox_status(db: Session, message_sid: str, status_value: str, payload: dict) -> None:
    now = datetime.utcnow()
    delivered_at = now if status_value == "delivered" else None
    bounced_at = now if status_value == "bounced" else None
    db.execute(
        text(
            """
            UPDATE email_outbox
               SET delivery_status = :status,
                   delivery_detail = :detail,
                   delivered_at = COALESCE(delivered_at, :delivered_at),
                   bounced_at = COALESCE(bounced_at, :bounced_at),
                   updated_at = :updated_at
             WHERE provider_message_id = :sid
            """
        ),
        {
            "status": status_value,
            "detail": payload,
            "delivered_at": delivered_at,
            "bounced_at": bounced_at,
            "updated_at": now,
            "sid": message_sid,
        },
    )
    db.commit()


@router.post("/inbound")
async def inbound_sms(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _normalize_params(dict(form))
    account_sid = params.get("AccountSid")
    to_number = params.get("To")

    settings = _lookup_sms_settings(db, account_sid, to_number)
    if not settings:
        return {"ok": True, "reason": "unknown_number"}

    if settings.twilio_auth_token_enc:
        auth_token = decrypt_secret(settings.twilio_auth_token_enc)
        _validate_twilio_signature(request, params, auth_token)

    return {"ok": True}


@router.post("/status")
async def sms_status(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _normalize_params(dict(form))
    account_sid = params.get("AccountSid")
    to_number = params.get("To")

    settings = _lookup_sms_settings(db, account_sid, to_number)
    if settings and settings.twilio_auth_token_enc:
        auth_token = decrypt_secret(settings.twilio_auth_token_enc)
        _validate_twilio_signature(request, params, auth_token)

    message_sid = params.get("MessageSid")
    message_status = (params.get("MessageStatus") or "").lower()
    status_map = {
        "queued": "queued",
        "sending": "sent",
        "sent": "sent",
        "delivered": "delivered",
        "undelivered": "bounced",
        "failed": "bounced",
    }
    mapped_status = status_map.get(message_status, "queued")

    if message_sid:
        _update_outbox_status(db, message_sid, mapped_status, params)

    return {"ok": True}
