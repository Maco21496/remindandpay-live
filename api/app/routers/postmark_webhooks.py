# app/routers/postmark_webhooks.py
from __future__ import annotations
import os, json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext

from ..database import get_db
from ..models import EmailOutbox  # and we’ll write events via simple SQL

router = APIRouter(prefix="/api/postmark", tags=["postmark"])
basic = HTTPBasic()

PM_USER = os.getenv("POSTMARK_WEBHOOK_USER", "")
PM_PASS = os.getenv("POSTMARK_WEBHOOK_PASS", "")

# --- helpers -------------------------------------------------

def _require_basic(creds: HTTPBasicCredentials = Depends(basic)):
    if not PM_USER or not PM_PASS:
        # If you forget to set env, fail closed.
        raise HTTPException(401, "Webhook auth not configured")
    if creds.username != PM_USER or creds.password != PM_PASS:
        raise HTTPException(401, "Unauthorized")

def _ts(v: Optional[str]) -> datetime:
    if not v:
        return datetime.utcnow()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)  # naive UTC
        return dt
    except Exception:
        return datetime.utcnow()

def _json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"

@router.post("/webhook")
async def postmark_webhook(
    request: Request,
    _auth = Depends(_require_basic),
    db: Session = Depends(get_db),
):
    # Parse JSON (fail closed but return 200 to avoid retries storms)
    try:
        data = await request.json()
    except Exception:
        return {"ok": True, "ignored": True, "reason": "bad_json"}

    record_type = str(data.get("RecordType") or "").strip()
    msg_id = str(data.get("MessageID") or data.get("OriginalMessageID") or "").strip()

    if not record_type or not msg_id:
        return {"ok": True, "ignored": True, "reason": "missing_recordtype_or_messageid"}

    # Look up the outbox row by provider MessageID (Postmark GUID)
    outbox: Optional[EmailOutbox] = (
        db.query(EmailOutbox)
          .filter(EmailOutbox.provider_message_id == msg_id)
          .first()
    )

    # If we don't find it, we can't insert into delivery_events (FK NOT NULL).
    # Just 200 OK so Postmark doesn't retry; next webhook or reconciliation can attach later.
    if not outbox:
        return {"ok": True, "queued": False, "note": "outbox_not_found"}

    # Ensure provider fields are set
    outbox.provider = "postmark"
    if not outbox.provider_message_id:
        outbox.provider_message_id = msg_id

    # Map core outcomes
    if record_type == "Delivery":
        outbox.delivery_status = "delivered"
        outbox.delivered_at = _ts(data.get("DeliveredAt"))
        outbox.delivery_detail = data
    elif record_type == "Bounce":
        outbox.delivery_status = "bounced"
        outbox.bounced_at = _ts(data.get("BouncedAt"))
        outbox.delivery_detail = data
    elif record_type == "SpamComplaint":
        outbox.delivery_status = "complained"
        outbox.complained_at = _ts(data.get("ReceivedAt"))
        outbox.delivery_detail = data
    elif record_type in ("Open", "Click"):
        # Optional signals — keep the latest payload for diagnostics/analytics
        outbox.delivery_detail = data
        # If you later add opened_at/open_count/clicked_at, update them here.
    else:
        # Unknown/unused types — still hold last payload for debugging
        outbox.delivery_detail = data

    # Derive a canonical event timestamp (falls back across known fields)
    event_when = _ts(
        data.get("DeliveredAt")
        or data.get("BouncedAt")
        or data.get("ReceivedAt")
        or data.get("Timestamp")
    )

    # Idempotency key if provider supplies one (BounceID, ClickID, etc.)
    provider_event_id = (
        str(data.get("ID") or data.get("BounceID") or data.get("ClickID") or "").strip() or None
    )

    # Persist an event row (IGNORE duplicates by uq on provider_event_id when present)
    db.execute(sqltext("""
        INSERT IGNORE INTO delivery_events
            (outbox_id, provider_message_id, record_type, event_at, payload_json, provider_event_id)
        VALUES
            (:oid, :pmid, :rt, :at, :js, :eid)
    """), {
        "oid": outbox.id,
        "pmid": msg_id,
        "rt": record_type,
        "at": event_when,             # naive UTC
        "js": _json(data),
        "eid": provider_event_id,
    })

    db.add(outbox)
    db.commit()
    return {"ok": True}
