# app/routers/settings.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, List
from datetime import time as dtime
from sqlalchemy.orm import Session

from fastapi import Depends, UploadFile, File
from pydantic import BaseModel, validator
from zoneinfo import available_timezones

from ..shared import APIRouter
from ..database import get_db
from ..models import AppSettings
from .auth import require_user
from ..initial_user_setup import run_initial_user_setup

router = APIRouter(prefix="/api/settings", tags=["settings"])

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

@router.post("/restore_defaults")
def restore_defaults(db: Session = Depends(get_db), user = Depends(require_user)):
    stats = run_initial_user_setup(
        db, user.id,
        seed_globals=True,
        seed_templates=True,
        overwrite_templates=False  # set True if you want a "factory reset" behavior
    )
    return {"ok": True, "stats": stats}

