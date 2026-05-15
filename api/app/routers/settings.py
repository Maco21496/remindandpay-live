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

from ..shared import APIRouter
from ..database import get_db
from ..models import AppSettings, AccountBillingProfile, SmsCreditLedger
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
    billing_debug = os.getenv("BILLING_DEBUG_INVOICES", "").strip().lower() in {"1", "true", "yes", "on"}
    profile = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user.id).first()
    if not profile or not profile.stripe_customer_id:
        return {"invoices": []}

    stripe_client = _get_stripe_client()
    if not stripe_client.api_key:
        return {"invoices": []}

    limit = max(1, min(int(limit or 20), 100))
    invoices = stripe_client.Invoice.list(customer=profile.stripe_customer_id, limit=limit)

    rows = []
    ledger_topups_raw = (
        db.query(SmsCreditLedger)
        .filter(SmsCreditLedger.user_id == user.id)
        .filter(SmsCreditLedger.reason == "stripe_topup")
        .order_by(SmsCreditLedger.created_at.desc())
        .limit(limit * 3)
        .all()
    )

    # Collapse duplicate ledger rows for the same topup (e.g. legacy + new reference formats).
    ledger_topups = []
    seen_ledger_keys = set()
    for entry in ledger_topups_raw:
        details = entry.details if isinstance(entry.details, dict) else {}
        pi = str(details.get("payment_intent_id") or "").strip()
        session_id = str(details.get("stripe_session_id") or "").strip()
        dedupe_key = f"pi:{pi}" if pi else (f"cs:{session_id}" if session_id else f"ref:{entry.reference_id or entry.id}")
        if dedupe_key in seen_ledger_keys:
            continue
        seen_ledger_keys.add(dedupe_key)
        ledger_topups.append(entry)
        if len(ledger_topups) >= limit:
            break

    topup_payment_intents = set()
    topup_checkout_sessions = set()
    for entry in ledger_topups:
        details = entry.details if isinstance(entry.details, dict) else {}
        pi = str(details.get("payment_intent_id") or "").strip()
        if pi:
            topup_payment_intents.add(pi)
        session_id = str(details.get("stripe_session_id") or "").strip()
        if session_id:
            topup_checkout_sessions.add(session_id)
        if billing_debug:
            logger.info("billing_debug ledger_topup user_id=%s ledger_id=%s pi=%s session_id=%s ref=%s", user.id, entry.id, pi, session_id, entry.reference_id)

    seen_topup_payment_intents = set()
    seen_topup_checkout_sessions = set()
    for inv in invoices.auto_paging_iter():
        metadata = _stripe_metadata_dict(getattr(inv, "metadata", None))
        if billing_debug:
            logger.info("billing_debug stripe_invoice_list user_id=%s invoice_id=%s customer=%s payment_intent=%s metadata_kind=%s metadata_session=%s has_pdf=%s", user.id, getattr(inv, "id", None), getattr(inv, "customer", None), getattr(inv, "payment_intent", None), metadata.get("kind"), metadata.get("stripe_session_id"), bool(getattr(inv, "invoice_pdf", None)))
        inv_sub = getattr(inv, "subscription", None)
        invoice_pi = str(getattr(inv, "payment_intent", "") or "").strip()
        metadata_pi = str(metadata.get("payment_intent_id") or "").strip()
        payment_intent_id = invoice_pi or metadata_pi

        metadata_session_id = str(metadata.get("stripe_session_id") or "").strip()
        is_topup = (
            (metadata.get("kind") == "sms_topup")
            or (payment_intent_id in topup_payment_intents)
            or (metadata_session_id in topup_checkout_sessions)
        )
        kind = "membership" if inv_sub else ("topup" if is_topup else "other")
        if kind == "topup":
            if payment_intent_id:
                seen_topup_payment_intents.add(payment_intent_id)
            if metadata_session_id:
                seen_topup_checkout_sessions.add(metadata_session_id)

        rows.append(_stripe_invoice_to_row(inv, kind=kind))

    # Backfill existing topups processed before invoice_creation was enabled in Checkout.
    for entry in ledger_topups:
        details = entry.details if isinstance(entry.details, dict) else {}
        pi = str(details.get("payment_intent_id") or "").strip()
        session_id = str(details.get("stripe_session_id") or "").strip()
        if (pi and pi in seen_topup_payment_intents) or (session_id and session_id in seen_topup_checkout_sessions):
            continue

        stripe_invoice = _find_stripe_invoice_for_topup(
            stripe_client,
            customer_id=profile.stripe_customer_id,
            payment_intent_id=pi,
            checkout_session_id=session_id,
            billing_debug=billing_debug,
            debug_user_id=user.id,
        )
        if stripe_invoice:
            rows.append(_stripe_invoice_to_row(stripe_invoice, kind="topup"))
            seen_topup_payment_intents.add(pi)
            continue

        created_ts = int(entry.created_at.timestamp()) if entry.created_at else None
        currency, amount_paid = _ledger_topup_money_from_details(details)
        rows.append({
            "id": entry.reference_id or f"ledger:{entry.id}",
            "number": None,
            "status": "paid",
            "currency": currency,
            "amount_due": amount_paid,
            "amount_paid": amount_paid,
            "created": created_ts,
            "hosted_invoice_url": None,
            "invoice_pdf": None,
            "kind": "topup",
        })

    rows.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return {"invoices": rows[:limit]}

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


def _find_stripe_invoice_for_topup(
    stripe_client,
    *,
    customer_id: str,
    payment_intent_id: str,
    checkout_session_id: str,
    billing_debug: bool = False,
    debug_user_id: int | None = None,
):
    # First try direct PI->invoice lookup (works even if invoice is on another Stripe customer).
    if payment_intent_id:
        try:
            pi = stripe_client.PaymentIntent.retrieve(payment_intent_id)
            invoice_id = _stripe_obj_id(getattr(pi, "invoice", None))
            if billing_debug:
                logger.info("billing_debug pi_lookup user_id=%s pi=%s pi_customer=%s pi_invoice=%s pi_latest_charge=%s", debug_user_id, payment_intent_id, _stripe_obj_id(getattr(pi, "customer", None)), invoice_id, _stripe_obj_id(getattr(pi, "latest_charge", None)))
            if not invoice_id:
                latest_charge_id = _stripe_obj_id(getattr(pi, "latest_charge", None))
                if latest_charge_id:
                    charge = stripe_client.Charge.retrieve(latest_charge_id)
                    invoice_id = _stripe_obj_id(getattr(charge, "invoice", None))
                    if billing_debug:
                        logger.info("billing_debug charge_lookup user_id=%s pi=%s charge=%s charge_invoice=%s", debug_user_id, payment_intent_id, latest_charge_id, invoice_id)
            if invoice_id:
                inv = stripe_client.Invoice.retrieve(invoice_id)
                if billing_debug:
                    logger.info("billing_debug pi_invoice_resolved user_id=%s pi=%s invoice_id=%s has_pdf=%s", debug_user_id, payment_intent_id, getattr(inv, "id", None), bool(getattr(inv, "invoice_pdf", None)))
                return inv
        except Exception as exc:
            if billing_debug:
                logger.exception("billing_debug pi_invoice_lookup_failed user_id=%s pi=%s error=%s", debug_user_id, payment_intent_id, exc)

    if not payment_intent_id and not checkout_session_id:
        return None
    try:
        invoices = stripe_client.Invoice.list(customer=customer_id, limit=100)
    except Exception as exc:
        if billing_debug:
            logger.exception("billing_debug invoice_list_failed user_id=%s customer_id=%s error=%s", debug_user_id, customer_id, exc)
        return None
    for inv in invoices.auto_paging_iter():
        inv_pi = str(getattr(inv, "payment_intent", "") or "").strip()
        if payment_intent_id and inv_pi == payment_intent_id:
            return inv
        metadata = _stripe_metadata_dict(getattr(inv, "metadata", None))
        inv_session = str(metadata.get("stripe_session_id") or "").strip()
        if checkout_session_id and inv_session == checkout_session_id:
            return inv
    return None


def _stripe_obj_id(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()
    return str(getattr(value, "id", "") or "").strip()


def _ledger_topup_money_from_details(details: dict) -> tuple[str | None, int | None]:
    package_key = str((details or {}).get("package_key") or "").strip()
    if package_key.isdigit():
        # Current topup packages are keyed by GBP amount (e.g. "10", "25").
        pounds = int(package_key)
        return "GBP", pounds * 100
    return None, None


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
