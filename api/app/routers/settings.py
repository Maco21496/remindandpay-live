# app/routers/settings.py
from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Optional, List
from datetime import time as dtime, datetime
from sqlalchemy.orm import Session

from fastapi import Depends, UploadFile, File
from pydantic import BaseModel, validator
from zoneinfo import available_timezones

from ..shared import APIRouter, HTTPException
from ..database import get_db
from ..models import AppSettings, AccountBillingProfile, AccountBillingTransaction
from .auth import require_user
from ..initial_user_setup import run_initial_user_setup
from ..services.billing_trial import ensure_billing_profile

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR   = PROJECT_ROOT / "web" / "static"

# ---------- helpers ----------
def _get_for_user(db, user_id: int) -> AppSettings:
    s = db.query(AppSettings).filter(AppSettings.user_id == user_id).first()
    if not s:
        s = AppSettings(user_id=user_id)  # DB defaults will fill in
        db.add(s); db.commit(); db.refresh(s)
    return s

def _sanitize_time_format(v: Optional[str]) -> str:
    v = (v or "").lower()
    return "12h" if v == "12h" else "24h"

def _sanitize_date_locale(v: Optional[str]) -> str:
    v = (v or "en-GB")
    return "en-US" if v == "en-US" else "en-GB"

def _sanitize_country(v: Optional[str]) -> str:
    return (v or "GB").upper()[:2]

def _sanitize_timezone(v: Optional[str]) -> str:
    tz = (v or "").strip() or "UTC"
    return tz if tz in available_timezones() else "UTC"

def _sanitize_hhmm(v: Optional[str]) -> str:
    s = (v or "").strip()
    if ":" in s:
        hh, mm = s.split(":", 1)
        if hh.isdigit() and mm.isdigit():
            h, m = int(hh), int(mm)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
    return "14:00"

def _parse_hhmm_to_time(v: Optional[str]) -> dtime:
    """'HH:MM' -> datetime.time"""
    s = _sanitize_hhmm(v)
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))

def _time_to_hhmm(v) -> str:
    """datetime.time | str | None -> 'HH:MM'"""
    if hasattr(v, "strftime"):
        return v.strftime("%H:%M")
    if isinstance(v, str) and ":" in v:
        return _sanitize_hhmm(v)
    return "14:00"

# ---------- schemas ----------
class SettingsIn(BaseModel):
    date_locale: Optional[str] = None
    time_format: Optional[str] = None
    default_country: Optional[str] = None
    currency: Optional[str] = None
    org_address: Optional[str] = None
    timezone: Optional[str] = None
    default_send_time: Optional[str] = None
    chase_style: Optional[str] = None  # 'gentle'|'firm'|'aggressive'|'custom'
    theme: Optional[str] = None        # data-theme name or 'custom'
    brand_color: Optional[str] = None  # '#RRGGBB'

    @validator("timezone")
    def _tz_ok(cls, v):
        if v is None:
            return v
        if v not in available_timezones():
            raise ValueError("Invalid timezone")
        return v

    @validator("default_send_time")
    def _time_ok(cls, v):
        if v is None:
            return v
        s = _sanitize_hhmm(v)
        if s != v:
            raise ValueError("default_send_time must be 'HH:MM' (00:00–23:59)")
        return v

class SettingsOut(BaseModel):
    date_locale: str
    time_format: str
    default_country: str
    currency: str
    org_address: str
    org_logo_url: str
    timezone: str
    default_send_time: str
    chase_style: str
    theme: Optional[str] = None
    brand_color: Optional[str] = None

# ---------- routes ----------
@router.get("", response_model=SettingsOut)
def get_settings(db=Depends(get_db), user=Depends(require_user)):
    s = _get_for_user(db, user.id)
    return {
        "date_locale": s.date_locale,
        "time_format": s.time_format,
        "default_country": s.default_country or "GB",
        "currency": getattr(s, "currency", None) or "GBP",
        "org_address": s.org_address or "",
        "org_logo_url": s.org_logo_url or "",
        "timezone": s.timezone or "UTC",
        "default_send_time": _time_to_hhmm(getattr(s, "default_send_time", None)),
        "chase_style": getattr(s, "chase_style", "gentle") or "gentle",
        "theme": getattr(s, "theme", None),
        "brand_color": getattr(s, "brand_color", None),
    }

@router.post("", response_model=SettingsOut)
def update_settings(body: SettingsIn, db=Depends(get_db), user=Depends(require_user)):
    s = _get_for_user(db, user.id)

    if body.date_locale is not None:
        s.date_locale = _sanitize_date_locale(body.date_locale)
    if body.time_format is not None:
        s.time_format = _sanitize_time_format(body.time_format)
    if body.default_country is not None:
        s.default_country = _sanitize_country(body.default_country)
    if body.currency is not None:
        cur = (body.currency or "").upper().strip()
        if cur in {"GBP","USD","EUR"}:
            s.currency = cur
    if body.org_address is not None:
        s.org_address = (body.org_address or None)
    if body.timezone is not None:
        s.timezone = _sanitize_timezone(body.timezone)
    if body.default_send_time is not None:
        s.default_send_time = _parse_hhmm_to_time(body.default_send_time)
    if body.chase_style in {"gentle","firm","aggressive","custom"}:
        s.chase_style = body.chase_style

    # theme + brand color
    if body.theme is not None:
        s.theme = (body.theme or None)
    if body.brand_color is not None:
        col = body.brand_color.strip() if isinstance(body.brand_color, str) else None
        if col and len(col) == 7 and col.startswith('#') and all(c in '0123456789abcdefABCDEF' for c in col[1:]):
            s.brand_color = col
        elif not col:
            s.brand_color = None

    db.add(s); db.commit(); db.refresh(s)

    return {
        "date_locale": s.date_locale,
        "time_format": s.time_format,
        "default_country": s.default_country or "GB",
        "currency": getattr(s, "currency", None) or "GBP",
        "org_address": s.org_address or "",
        "org_logo_url": s.org_logo_url or "",
        "timezone": s.timezone or "UTC",
        "default_send_time": _time_to_hhmm(s.default_send_time),
        "chase_style": getattr(s, "chase_style", "gentle") or "gentle",
        "theme": getattr(s, "theme", None),
        "brand_color": getattr(s, "brand_color", None),
    }

@router.get("/timezones", response_model=List[str])
def list_timezones():
    return sorted(available_timezones())

@router.post("/logo")
def upload_logo(file: UploadFile = File(...), db=Depends(get_db), user=Depends(require_user)):
    upload_dir = STATIC_DIR / "uploads" / f"u{user.id}" / "logo"
    upload_dir.mkdir(parents=True, exist_ok=True)

    _, ext = os.path.splitext(file.filename or "")
    ext = (ext or ".png").lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        ext = ".jpg"

    disk_path = upload_dir / f"company_logo{ext}"
    with open(disk_path, "wb") as f:
        f.write(file.file.read())

    rel = disk_path.relative_to(STATIC_DIR).as_posix()
    url_path = f"/static/{rel}"

    s = _get_for_user(db, user.id)
    s.org_logo_url = url_path
    db.add(s); db.commit(); db.refresh(s)
    return {"org_logo_url": s.org_logo_url}

@router.delete("/logo")
def delete_logo(db=Depends(get_db), user=Depends(require_user)):
    s = _get_for_user(db, user.id)
    s.org_logo_url = None
    db.add(s); db.commit()
    return {"ok": True}



@router.get("/billing")
def get_billing_settings(db: Session = Depends(get_db), user=Depends(require_user)):
    profile = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user.id).first()
    if not profile:
        profile = ensure_billing_profile(db, user)
        db.commit()
        db.refresh(profile)

    now = datetime.utcnow()
    days_left = max(0, (profile.trial_ends_at.date() - now.date()).days) if profile.trial_ends_at else 0
    effective_status = profile.subscription_status
    if effective_status == "trialing" and profile.trial_ends_at and profile.trial_ends_at < now:
        effective_status = "trial_expired"

    return {
        "trial_days_assigned": profile.trial_days_assigned,
        "trial_started_at": profile.trial_started_at.isoformat() if profile.trial_started_at else None,
        "trial_ends_at": profile.trial_ends_at.isoformat() if profile.trial_ends_at else None,
        "trial_days_left": days_left,
        "subscription_status": effective_status,
        "stripe_customer_id": profile.stripe_customer_id,
        "stripe_subscription_id": profile.stripe_subscription_id,
    }



@router.get("/billing/invoices")
def get_billing_invoices(limit: int = 20, db: Session = Depends(get_db), user=Depends(require_user)):
    limit = max(1, min(int(limit or 20), 100))
    rows = (
        db.query(AccountBillingTransaction)
        .filter(AccountBillingTransaction.user_id == user.id)
        .order_by(AccountBillingTransaction.created_at.desc())
        .limit(limit)
        .all()
    )

    invoices = []
    for row in rows:
        invoices.append({
            "id": row.id,
            "number": row.stripe_invoice_id,
            "status": row.status,
            "currency": (row.currency or "").upper(),
            "amount_due": row.amount_minor,
            "amount_paid": row.amount_minor,
            "created": int(row.created_at.timestamp()) if row.created_at else None,
            "hosted_invoice_url": None,
            "invoice_pdf": None,
            "kind": row.product_type,
            "transaction_type": row.transaction_type,
            "product_code": row.product_code,
            "parent_transaction_id": row.parent_transaction_id,
            "stripe_invoice_id": row.stripe_invoice_id,
            "stripe_credit_note_id": row.stripe_credit_note_id,
        })

    return {"invoices": invoices}




@router.get("/billing/documents/invoice/{transaction_id}")
def get_billing_invoice_document(transaction_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    row = db.query(AccountBillingTransaction).filter(AccountBillingTransaction.id == transaction_id, AccountBillingTransaction.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if not row.stripe_invoice_id:
        return {"available": False, "message": "Invoice document is unavailable for this transaction."}
    stripe_client = _get_stripe_client()
    invoices = []

    for row in rows:
        details = dict(row.details) if isinstance(row.details, dict) else {}

        document_error = None
        stripe_invoice_number = details.get("stripe_invoice_number")
        stripe_invoice_status = None
        stripe_invoice_finalized_at = None
        hosted_invoice_url = None
        invoice_pdf = None
        credit_note_url = None
        credit_note_pdf = None

        if row.stripe_invoice_id:
            try:
                inv = stripe_client.Invoice.retrieve(row.stripe_invoice_id)
                stripe_invoice_number = getattr(inv, "number", None) or stripe_invoice_number
                stripe_invoice_status = getattr(inv, "status", None)

                status_transitions = getattr(inv, "status_transitions", None)
                stripe_invoice_finalized_at = (
                    getattr(status_transitions, "finalized_at", None)
                    if status_transitions is not None
                    else None
                )

                hosted_invoice_url = getattr(inv, "hosted_invoice_url", None)
                invoice_pdf = getattr(inv, "invoice_pdf", None)
            except Exception as exc:
                document_error = f"invoice_retrieve_failed:{exc.__class__.__name__}:{exc}"

        if row.stripe_credit_note_id:
            try:
                cn = stripe_client.CreditNote.retrieve(row.stripe_credit_note_id)
                credit_note_pdf = getattr(cn, "pdf", None)
                credit_note_url = getattr(cn, "pdf", None) or getattr(cn, "credit_note_pdf", None)
            except Exception as exc:
                if not document_error:
                    document_error = f"credit_note_retrieve_failed:{exc.__class__.__name__}:{exc}"

        invoices.append({
            "id": row.id,
            "number": stripe_invoice_number or row.stripe_invoice_id or str(row.id),
            "status": row.status,
            "currency": (row.currency or "").upper(),
            "amount_due": row.amount_minor,
            "amount_paid": row.amount_minor if row.status == "succeeded" else 0,
            "created": int(row.created_at.timestamp()) if row.created_at else None,
            "hosted_invoice_url": hosted_invoice_url,
            "invoice_pdf": invoice_pdf,
            "kind": row.product_type,
            "transaction_type": row.transaction_type,
            "product_code": row.product_code,
            "parent_transaction_id": row.parent_transaction_id,
            "stripe_invoice_id": row.stripe_invoice_id,
            "stripe_invoice_number": stripe_invoice_number,
            "stripe_invoice_status": stripe_invoice_status,
            "stripe_invoice_finalized_at": stripe_invoice_finalized_at,
            "stripe_credit_note_id": row.stripe_credit_note_id,
            "credit_note_url": credit_note_url,
            "credit_note_pdf": credit_note_pdf,
            "document_error": document_error,
        })

    return {
        "billing_invoice_route_version": "local_txn_doc_hydration_v3",
        "invoices": invoices,
    }


@router.get("/billing/documents/invoice/{transaction_id}")
def get_billing_invoice_document(transaction_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    row = (
        db.query(AccountBillingTransaction)
        .filter(
            AccountBillingTransaction.id == transaction_id,
            AccountBillingTransaction.user_id == user.id,
        )
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if not row.stripe_invoice_id:
        return {
            "available": False,
            "message": "Invoice document is unavailable for this transaction.",
        }

    stripe_client = _get_stripe_client()

    try:
        inv = stripe_client.Invoice.retrieve(row.stripe_invoice_id)
    except Exception as exc:
        return {
            "available": False,
            "stripe_invoice_id": row.stripe_invoice_id,
            "message": f"Unable to retrieve invoice document: {exc.__class__.__name__}: {exc}",
        }

    details = dict(row.details) if isinstance(row.details, dict) else {}
    stripe_invoice_number = getattr(inv, "number", None) or details.get("stripe_invoice_number")

    return {
        "available": bool(getattr(inv, "invoice_pdf", None) or getattr(inv, "hosted_invoice_url", None)),
        "stripe_invoice_id": row.stripe_invoice_id,
        "stripe_invoice_number": stripe_invoice_number,
        "invoice_pdf": getattr(inv, "invoice_pdf", None),
        "hosted_invoice_url": getattr(inv, "hosted_invoice_url", None),
    }


@router.get("/billing/documents/credit-note/{transaction_id}")
def get_billing_credit_note_document(transaction_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    row = (
        db.query(AccountBillingTransaction)
        .filter(
            AccountBillingTransaction.id == transaction_id,
            AccountBillingTransaction.user_id == user.id,
        )
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if not row.stripe_credit_note_id:
        return {
            "available": False,
            "message": "Credit note is unavailable for this transaction.",
        }

    stripe_client = _get_stripe_client()

    try:
        cn = stripe_client.CreditNote.retrieve(row.stripe_credit_note_id)
    except Exception as exc:
        return {
            "available": False,
            "stripe_credit_note_id": row.stripe_credit_note_id,
            "message": f"Unable to retrieve credit note: {exc.__class__.__name__}: {exc}",
        }

    return {
        "available": bool(getattr(cn, "pdf", None)),
        "stripe_credit_note_id": row.stripe_credit_note_id,
        "credit_note_pdf": getattr(cn, "pdf", None),
    }


@router.post("/restore_defaults")
def restore_defaults(db: Session = Depends(get_db), user = Depends(require_user)):
    stats = run_initial_user_setup(
        db, user.id,
        seed_globals=True,
        seed_templates=True,
        overwrite_templates=False  # set True if you want a "factory reset" behavior
    )
    return {"ok": True, "stats": stats}



def _stripe_invoice_to_row(inv, *, kind: str) -> dict:
    return {
        "id": inv.id,
        "number": getattr(inv, "number", None),
        "status": getattr(inv, "status", None),
        "currency": getattr(inv, "currency", "").upper() if getattr(inv, "currency", None) else None,
        "amount_due": getattr(inv, "amount_due", None),
        "amount_paid": getattr(inv, "amount_paid", None),
        "created": getattr(inv, "created", None),
        "hosted_invoice_url": getattr(inv, "hosted_invoice_url", None),
        "invoice_pdf": getattr(inv, "invoice_pdf", None),
        "kind": kind,
    }


def _get_stripe_client():
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    return stripe



def _stripe_metadata_dict(raw_metadata) -> dict:
    if raw_metadata is None:
        return {}
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if hasattr(raw_metadata, "to_dict"):
        converted = raw_metadata.to_dict()
        if isinstance(converted, dict):
            return converted
    if hasattr(raw_metadata, "items"):
        return {str(k): v for k, v in raw_metadata.items()}
    return {}
