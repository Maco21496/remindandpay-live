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
import requests

from ..crypto_secrets import decrypt_secret
from ..database import get_db
from ..models import AccountSmsSettings, EmailOutbox, SmsCreditLedger, SmsPricingSettings, SmsWebhookLog

router = APIRouter(prefix="/api/sms/webhooks", tags=["sms-webhooks"])


def _normalize_params(form: dict) -> dict:
    return {k: v if not isinstance(v, list) else (v[0] if v else "") for k, v in form.items()}


def _twilio_auth_headers(username: str, password: str) -> tuple[str, str]:
    return (username, password)


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


def _update_outbox_status(db: Session, message_sid: str, status_value: str, payload: dict) -> int:
    now = datetime.utcnow()
    delivered_at = now if status_value == "delivered" else None
    bounced_at = now if status_value == "bounced" else None
    result = db.execute(
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
    return int(getattr(result, "rowcount", 0) or 0)


def _lookup_outbox_by_sid(db: Session, message_sid: str) -> Optional[EmailOutbox]:
    if not message_sid:
        return None
    return (
        db.query(EmailOutbox)
        .filter(EmailOutbox.provider_message_id == message_sid)
        .first()
    )


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


def _twilio_fetch_message_details(account_sid: str, message_sid: str) -> dict:
    if not account_sid or not message_sid:
        return {}
    api_key_sid = (os.getenv("TWILIO_API_KEY_SID", "") or "").strip()
    api_key_secret = (os.getenv("TWILIO_API_KEY_SECRET", "") or "").strip()
    master_sid = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
    master_auth_token = (os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()
    if not api_key_sid or not api_key_secret:
        return {}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages/{message_sid}.json"
    primary_auth = _twilio_auth_headers(api_key_sid, api_key_secret)
    fallback_auth = (
        _twilio_auth_headers(master_sid, master_auth_token)
        if master_sid and master_auth_token
        else None
    )
    r = requests.get(url, auth=primary_auth, timeout=20)
    if r.status_code == 401 and fallback_auth:
        r = requests.get(url, auth=fallback_auth, timeout=20)
    if not r.ok:
        return {}
    return r.json() or {}


def _record_sms_debit(
    db: Session,
    settings: Optional[AccountSmsSettings],
    params: dict,
) -> None:
    message_sid = (params.get("MessageSid") or "").strip()
    if not message_sid:
        return
    outbox = _lookup_outbox_by_sid(db, message_sid)
    if not settings and not outbox:
        return
    status = (params.get("MessageStatus") or "").lower()
    if status not in {"sent", "delivered"}:
        return
    existing = (
        db.query(SmsCreditLedger.id)
        .filter(
            SmsCreditLedger.user_id == (settings.user_id if settings else outbox.user_id),
            SmsCreditLedger.entry_type == "debit",
            SmsCreditLedger.reference_id == message_sid,
        )
        .first()
    )
    if existing:
        return

    num_segments_value = params.get("NumSegments")
    if num_segments_value in (None, ""):
        details = _twilio_fetch_message_details(
            (params.get("AccountSid") or "").strip(),
            message_sid,
        )
        num_segments_value = details.get("num_segments")
    try:
        num_segments = int(num_segments_value or 1)
    except (TypeError, ValueError):
        num_segments = 1
    num_segments = max(1, num_segments)
    pricing = _ensure_pricing(db)
    credits_per_segment = int(pricing.sms_send_cost or 0)
    total_credits = max(0, num_segments * credits_per_segment)
    if total_credits <= 0:
        return

    user_id = settings.user_id if settings else outbox.user_id
    to_number = params.get("To") or (outbox.to_email if outbox else None)
    from_number = params.get("From")
    entry = SmsCreditLedger(
        user_id=user_id,
        entry_type="debit",
        amount=total_credits,
        reason="sms_send",
        reference_id=message_sid,
        details={
            "message_sid": message_sid,
            "to": to_number,
            "from": from_number,
            "segments": num_segments,
            "status": status,
            "credits_per_segment": credits_per_segment,
            "outbox_id": outbox.id if outbox else None,
            "customer_id": outbox.customer_id if outbox else None,
        },
    )
    db.add(entry)
    db.commit()


def _log_sms_webhook(db: Session, kind: str, params: dict) -> None:
    if (os.getenv("TWILIO_LOG_WEBHOOKS", "") or "").strip().lower() not in {"1", "true", "yes"}:
        return
    record = SmsWebhookLog(
        kind=kind,
        account_sid=(params.get("AccountSid") or "").strip() or None,
        message_sid=(params.get("MessageSid") or "").strip() or None,
        payload=params,
    )
    db.add(record)
    db.commit()


def _lookup_outbox_by_sid(db: Session, message_sid: str) -> Optional[EmailOutbox]:
    if not message_sid:
        return None
    return (
        db.query(EmailOutbox)
        .filter(EmailOutbox.provider_message_id == message_sid)
        .first()
    )


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


def _twilio_fetch_message_details(account_sid: str, message_sid: str) -> dict:
    if not account_sid or not message_sid:
        return {}
    api_key_sid = (os.getenv("TWILIO_API_KEY_SID", "") or "").strip()
    api_key_secret = (os.getenv("TWILIO_API_KEY_SECRET", "") or "").strip()
    master_sid = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
    master_auth_token = (os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()
    if not api_key_sid or not api_key_secret:
        return {}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages/{message_sid}.json"
    primary_auth = _twilio_auth_headers(api_key_sid, api_key_secret)
    fallback_auth = (
        _twilio_auth_headers(master_sid, master_auth_token)
        if master_sid and master_auth_token
        else None
    )
    r = requests.get(url, auth=primary_auth, timeout=20)
    if r.status_code == 401 and fallback_auth:
        r = requests.get(url, auth=fallback_auth, timeout=20)
    if not r.ok:
        return {}
    return r.json() or {}


def _record_sms_debit(
    db: Session,
    settings: Optional[AccountSmsSettings],
    params: dict,
) -> None:
    message_sid = (params.get("MessageSid") or "").strip()
    if not message_sid:
        return
    outbox = _lookup_outbox_by_sid(db, message_sid)
    if not settings and not outbox:
        return
    status = (params.get("MessageStatus") or "").lower()
    if status not in {"sent", "delivered"}:
        return
    existing = (
        db.query(SmsCreditLedger.id)
        .filter(
            SmsCreditLedger.user_id == (settings.user_id if settings else outbox.user_id),
            SmsCreditLedger.entry_type == "debit",
            SmsCreditLedger.reference_id == message_sid,
        )
        .first()
    )
    if existing:
        return

    num_segments_value = params.get("NumSegments")
    if num_segments_value in (None, ""):
        details = _twilio_fetch_message_details(
            (params.get("AccountSid") or "").strip(),
            message_sid,
        )
        num_segments_value = details.get("num_segments")
    try:
        num_segments = int(num_segments_value or 1)
    except (TypeError, ValueError):
        num_segments = 1
    num_segments = max(1, num_segments)
    pricing = _ensure_pricing(db)
    credits_per_segment = int(pricing.sms_send_cost or 0)
    total_credits = max(0, num_segments * credits_per_segment)
    if total_credits <= 0:
        return

    user_id = settings.user_id if settings else outbox.user_id
    to_number = params.get("To") or (outbox.to_email if outbox else None)
    from_number = params.get("From")
    entry = SmsCreditLedger(
        user_id=user_id,
        entry_type="debit",
        amount=total_credits,
        reason="sms_send",
        reference_id=message_sid,
        details={
            "message_sid": message_sid,
            "to": to_number,
            "from": from_number,
            "segments": num_segments,
            "status": status,
            "credits_per_segment": credits_per_segment,
            "outbox_id": outbox.id if outbox else None,
            "customer_id": outbox.customer_id if outbox else None,
        },
    )
    db.add(entry)
    db.commit()


def _log_sms_webhook(db: Session, kind: str, params: dict) -> None:
    if (os.getenv("TWILIO_LOG_WEBHOOKS", "") or "").strip().lower() not in {"1", "true", "yes"}:
        return
    record = SmsWebhookLog(
        kind=kind,
        account_sid=(params.get("AccountSid") or "").strip() or None,
        message_sid=(params.get("MessageSid") or "").strip() or None,
        payload=params,
    )
    db.add(record)
    db.commit()


@router.post("/inbound")
async def inbound_sms(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _normalize_params(dict(form))
    _log_sms_webhook(db, "inbound", params)
    account_sid = params.get("AccountSid")
    to_number = params.get("To")

    settings = _lookup_sms_settings(db, account_sid, to_number)
    if not settings:
        return {"ok": True, "reason": "unknown_number"}

    if settings.twilio_auth_token_enc:
        auth_token = decrypt_secret(settings.twilio_auth_token_enc)
        try:
            _validate_twilio_signature(request, params, auth_token)
        except HTTPException as exc:
            _log_sms_webhook(
                db,
                "signature-error",
                {"error": exc.detail, "kind": "inbound", **params},
            )
            raise

    return {"ok": True}


@router.post("/status")
async def sms_status(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _normalize_params(dict(form))
    _log_sms_webhook(db, "status", params)
    account_sid = params.get("AccountSid")
    to_number = params.get("To")

    settings = _lookup_sms_settings(db, account_sid, to_number)
    if settings and settings.twilio_auth_token_enc:
        auth_token = decrypt_secret(settings.twilio_auth_token_enc)
        try:
            _validate_twilio_signature(request, params, auth_token)
        except HTTPException as exc:
            _log_sms_webhook(
                db,
                "signature-error",
                {"error": exc.detail, "kind": "status", **params},
            )
            raise

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
        updated = _update_outbox_status(db, message_sid, mapped_status, params)
        if updated == 0:
            _log_sms_webhook(
                db,
                "status-unmatched",
                {"note": "no outbox row updated", **params},
            )
        _record_sms_debit(db, settings, params)

    return {"ok": True}
