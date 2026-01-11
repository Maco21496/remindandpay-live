# FINAL VERSION OF api/app/routers/inbound_invoice_queue.py
from __future__ import annotations

import json
from typing import List, Dict, Any, Optional
from decimal import Decimal, InvalidOperation
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext, text, func, bindparam
from sqlalchemy.exc import IntegrityError

from ..database import get_db
from .auth import require_user
from ..models import Invoice, Customer, User
from ..calculate_due_date import compute_due_date
from ..shared import Field

router = APIRouter(prefix="/api/inbound/queue", tags=["inbound-queue"])


# ----------------------------
# Schemas
# ----------------------------

class InboundQueueItem(BaseModel):
    id: int
    received_at: str
    source: str
    original_filename: Optional[str]
    status: str
    error_message: Optional[str]
    fields: Optional[Dict[str, Any]]


class InboundQueueListOut(BaseModel):
    items: List[InboundQueueItem]


class PromoteIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


class PromoteOut(BaseModel):
    ok: bool
    imported: int
    failed: list[dict] = Field(default_factory=list)


# ----------------------------
# Helpers
# ----------------------------

def _clean_decimal_str(s: str) -> Decimal:
    raw = (s or "").strip().replace(",", "").replace("£", "")
    if raw == "":
        raise InvalidOperation("empty")
    return Decimal(raw)


def _parse_date_fuzzy(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    # ISO first
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        pass
    # Common invoice formats (aligned with invoices.py approach)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.replace(".", "/"), fmt)
        except Exception:
            continue
    # last try: tolerate 2025/11/12 style
    try:
        return datetime.fromisoformat(s.replace("/", "-"))
    except Exception:
        return None


def _parse_json_maybe(s_or_obj: Any) -> Optional[Dict[str, Any]]:
    """
    Accepts dict (return as-is), JSON string (loads), or anything else (None).
    """
    if s_or_obj is None:
        return None
    if isinstance(s_or_obj, dict):
        return s_or_obj
    if isinstance(s_or_obj, (bytes, bytearray)):
        try:
            return json.loads(s_or_obj.decode("utf-8", errors="replace"))
        except Exception:
            return None
    if isinstance(s_or_obj, str):
        try:
            return json.loads(s_or_obj)
        except Exception:
            return None
    return None

def _extract_fields_from_queue_row(row) -> dict:
    """
    Given a SQLAlchemy row from inbound_invoice_queue, return a dict of fields.
    Prefers extracted_text (your mapper output) and only falls back to payload_json.
    Supports both top-level keys and {"fields": {...}} wrappers.
    """
    import json

    def _parse(obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, (bytes, bytearray)):
            try:
                return json.loads(obj.decode("utf-8", errors="replace"))
            except Exception:
                return None
        if isinstance(obj, str):
            try:
                return json.loads(obj)
            except Exception:
                return None
        return None

    payload = _parse(getattr(row, "extracted_text", None))
    if payload is None:
        payload = _parse(getattr(row, "payload_json", None)) or {}

    if isinstance(payload, dict) and isinstance(payload.get("fields"), dict):
        return payload["fields"]
    return payload if isinstance(payload, dict) else {}



# ----------------------------
# Endpoints
# ----------------------------

@router.get("", response_model=InboundQueueListOut)
def list_queue_items(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """
    Return up to 200 most recent inbound_invoice_queue rows for the current user.
    We parse `extracted_text` JSON (what the UI shows in the table as "fields").
    """
    rows = db.execute(
        sqltext(
            """
            SELECT
                id,
                received_at,
                source,
                original_filename,
                extracted_text,
                status,
                error_message
            FROM inbound_invoice_queue
            WHERE user_id = :uid
            ORDER BY received_at DESC, id DESC
            LIMIT 200
            """
        ),
        {"uid": user.id},
    ).fetchall()

    items: List[InboundQueueItem] = []
    for r in rows:
        raw = getattr(r, "extracted_text", None)
        parsed = _parse_json_maybe(raw)

        rec_dt = getattr(r, "received_at", None)
        rec_str = rec_dt.isoformat() if rec_dt is not None else ""

        items.append(
            InboundQueueItem(
                id=int(r.id),
                received_at=rec_str,
                source=str(r.source),
                original_filename=getattr(r, "original_filename", None),
                status=str(r.status),
                error_message=getattr(r, "error_message", None),
                fields=parsed,
            )
        )

    return InboundQueueListOut(items=items)


@router.delete("/clear")
def clear_queue_for_user(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """
    Delete ALL inbound_invoice_queue rows for the current user.
    Intended for test data reset / clearing processed items.
    """
    result = db.execute(
        sqltext("DELETE FROM inbound_invoice_queue WHERE user_id = :uid"),
        {"uid": user.id},
    )
    db.commit()
    deleted = result.rowcount or 0
    return {"ok": True, "deleted": int(deleted)}


@router.delete("/{queue_id}")
def delete_queue_item(
    queue_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """
    Delete a single queue row (by id) for the current user.
    """
    result = db.execute(
        sqltext(
            """
            DELETE FROM inbound_invoice_queue
            WHERE id = :qid AND user_id = :uid
            """
        ),
        {"qid": queue_id, "uid": user.id},
    )
    db.commit()
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail="Queue row not found")
    return {"ok": True}

@router.post("/promote", response_model=PromoteOut)
def promote_invoices(
    p: PromoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not p.ids:
        return PromoteOut(ok=True, imported=0, failed=[])

    imported = 0
    failed: list[dict] = []

    sel = (
        text("""
            SELECT id, user_id, extracted_text, payload_json
              FROM inbound_invoice_queue
             WHERE user_id = :uid AND id IN :ids
        """).bindparams(bindparam("ids", expanding=True))
    )
    rows = db.execute(sel, {"uid": user.id, "ids": p.ids}).fetchall()
    by_id = {int(r.id): r for r in rows}

    for qid in p.ids:
        row = by_id.get(int(qid))
        if not row:
            failed.append({"id": qid, "error": "not_found"})
            continue

        try:
            f = _extract_fields_from_queue_row(row)

            inv_no   = (str(f.get("invoice_number") or "")).strip()
            amt_raw  = f.get("amount_due")
            issue_s  = (str(f.get("issue_date") or "")).strip()
            due_s    = (str(f.get("due_date") or "")).strip()
            cust_val = (str(f.get("_customer_lookup_value") or "")).strip()
            currency = (str(f.get("currency") or "GBP") or "GBP").strip()

            if not inv_no:
                raise ValueError("missing invoice_number")
            if amt_raw is None:
                raise ValueError("missing amount_due")
            if not issue_s:
                raise ValueError("missing issue_date")

            cust = (
                db.query(Customer)
                  .filter(Customer.user_id == user.id)
                  .filter(func.lower(Customer.name) == cust_val.lower())
                  .first()
                if cust_val else None
            )
            if not cust:
                raise ValueError("needs_customer")

            amount   = _clean_decimal_str(str(amt_raw))
            issue_dt = _parse_date_fuzzy(issue_s)
            if not issue_dt:
                raise ValueError("bad issue_date")

            if due_s:
                due_dt = _parse_date_fuzzy(due_s)
                if not due_dt:
                    raise ValueError("bad due_date")
            else:
                ttype = cust.terms_type or "net_30"
                tdays = cust.terms_days if (cust.terms_type == "custom") else None
                due_dt = compute_due_date(issue_dt, ttype, tdays)

            # Duplicate check (case-insensitive per customer)
            exists = (
                db.query(Invoice)
                  .filter(Invoice.user_id == user.id)
                  .filter(Invoice.customer_id == cust.id)
                  .filter(func.lower(Invoice.invoice_number) == inv_no.lower())
                  .first()
            )
            if exists:
                db.execute(
                    sqltext("UPDATE inbound_invoice_queue SET error_message = :m WHERE id = :id AND user_id = :uid"),
                    {"m": "duplicate_invoice_number", "id": qid, "uid": user.id},
                )
                failed.append({"id": qid, "error": "duplicate_invoice_number"})
                continue

            inv = Invoice(
                user_id=user.id,
                customer_id=cust.id,
                invoice_number=inv_no,
                amount_due=amount,
                currency=currency,
                issue_date=issue_dt,
                due_date=due_dt,
                status="chasing",
                terms_type=cust.terms_type,
                terms_days=cust.terms_days if cust.terms_type == "custom" else None,
            )
            db.add(inv)
            db.flush()  # may raise IntegrityError in rare race

            db.execute(
                sqltext("DELETE FROM inbound_invoice_queue WHERE id = :id AND user_id = :uid"),
                {"id": qid, "uid": user.id},
            )
            imported += 1

        except IntegrityError:
            db.rollback()
            db.execute(
                sqltext("UPDATE inbound_invoice_queue SET error_message = :m WHERE id = :id AND user_id = :uid"),
                {"m": "duplicate_invoice_number", "id": qid, "uid": user.id},
            )
            failed.append({"id": qid, "error": "duplicate_invoice_number"})
        except InvalidOperation:
            db.rollback()
            db.execute(
                sqltext("UPDATE inbound_invoice_queue SET error_message = :m WHERE id = :id AND user_id = :uid"),
                {"m": "bad_amount", "id": qid, "uid": user.id},
            )
            failed.append({"id": qid, "error": "bad_amount"})
        except ValueError as ve:
            db.rollback()
            db.execute(
                sqltext("UPDATE inbound_invoice_queue SET error_message = :m WHERE id = :id AND user_id = :uid"),
                {"m": str(ve), "id": qid, "uid": user.id},
            )
            failed.append({"id": qid, "error": str(ve)})
        except Exception as ex:
            db.rollback()
            db.execute(
                sqltext("UPDATE inbound_invoice_queue SET error_message = :m WHERE id = :id AND user_id = :uid"),
                {"m": f"unexpected:{type(ex).__name__}", "id": qid, "uid": user.id},
            )
            failed.append({"id": qid, "error": f"unexpected:{type(ex).__name__}"})

    db.commit()
    return PromoteOut(ok=True, imported=imported, failed=failed)