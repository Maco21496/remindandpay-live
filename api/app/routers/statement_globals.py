# app/routers/statement_globals.py
from __future__ import annotations

import json
from typing import Literal, Optional, List, Dict, Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from .auth import require_user

from fastapi.responses import PlainTextResponse
import traceback

from ..services.statement_globals_logic import ensure_global_rules


from .statement_reminders import _get_user_tz, _norm_time, set_to_idxs, _local_hhmm_next_utc



Freq = Literal["weekly", "monthly"]

router = APIRouter(prefix="/api/statement_globals", tags=["statement_globals"])

# ---------------------------- helpers ----------------------------

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

def _idx_to_dayname(i: int) -> str:
    i = max(0, min(6, int(i)))
    return DAY_NAMES[i]

def _set_to_idx(s: Optional[str], default: int) -> int:
    # MySQL SET returns "mon,tue" etc.; we only need the first item.
    if not s:
        return default
    first = s.split(",")[0].strip().lower()
    try:
        return DAY_NAMES.index(first)
    except ValueError:
        return default

def _json_first_int(val: Any, default: int) -> int:
    try:
        if val is None:
            return default
        if isinstance(val, str):
            val = json.loads(val)
        if isinstance(val, (list, tuple)) and val:
            return int(val[0])
        return int(val)
    except Exception:
        return default

def _hour_from_hhmm(s: Optional[str]) -> int:
    try:
        hhmm = _norm_time(s)          # <-- handles 57600, "16:00", etc.
        return int(hhmm.split(":")[0])
    except Exception:
        return 14

def _days_list_from_row(row: dict) -> list[int]:
    """
    Convert row's weekly/monthly day fields into the integer list the scheduler expects.
    - weekly: reminder_weekdays is a SET of tokens ('mon',...), convert to [0..6]
    - monthly: reminder_month_days is JSON array of ints
    """
    freq = row.get("reminder_frequency")
    if freq == "weekly":
        tokens = (row.get("reminder_weekdays") or "").split(",")
        map_idx = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        out = [map_idx[t.strip().lower()] for t in tokens if t.strip().lower() in map_idx]
        # we store only one for globals, but keep it generic
        return out or [0]
    else:
        # monthly
        try:
            import json
            arr = json.loads(row.get("reminder_month_days") or "[]")
            return [int(x) for x in arr] or [1]
        except Exception:
            return [1]

def _recompute_next_run_for_global(db: Session, user_id: int, freq: Freq) -> None:
    """
    Reads the (updated) global rule and writes reminder_next_run_utc based on:
    - reminder_time (HH:MM)
    - weekly: reminder_weekdays tokens -> [0..6]
    - monthly: reminder_month_days JSON -> [1..31]
    - user's timezone
    """
    row = db.execute(text("""
        SELECT id, reminder_frequency, reminder_time, reminder_weekdays, reminder_month_days
          FROM reminder_rules
         WHERE user_id=:uid AND is_global=1 AND reminder_type='statements' AND reminder_frequency=:freq
         LIMIT 1
    """), {"uid": user_id, "freq": freq}).mappings().first()
    if not row:
        return

    days_list = _days_list_from_row(dict(row))
    tz = _get_user_tz(db, user_id)
    hhmm = _norm_time(row["reminder_time"])
    next_utc = _local_hhmm_next_utc(freq, hhmm, days_list, tz)

    db.execute(text("""
        UPDATE reminder_rules
           SET reminder_next_run_utc = :nx
         WHERE id = :rid
         LIMIT 1
    """), {"nx": next_utc, "rid": row["id"]})
    db.commit()


# -------------------- ensure two global rules --------------------

# ------------------------ data accessors ------------------------

def get_global_rule(db: Session, user_id: int, frequency: Freq) -> Optional[Dict[str, Any]]:
    row = db.execute(
        text("""
            SELECT id, user_id, name, reminder_type, reminder_frequency,
                   reminder_time, reminder_weekdays, reminder_month_days,
                   reminder_enabled, is_global, created_at
              FROM reminder_rules
             WHERE user_id=:uid AND is_global=1
               AND reminder_type='statements'
               AND reminder_frequency=:freq
             LIMIT 1
        """),
        {"uid": user_id, "freq": frequency},
    ).mappings().first()
    return dict(row) if row else None

def update_global_rule(
    db: Session,
    user_id: int,
    frequency: Freq,
    *,
    enabled: Optional[bool] = None,
    time_hhmm: Optional[str] = None,   # "HH:MM"
    day_value: Optional[int] = None,   # weekly: 0..6, monthly: 1..31
) -> None:
    ensure_global_rules(db, user_id)

    sets: List[str] = []
    params: Dict[str, Any] = {"uid": user_id, "freq": frequency}

    if enabled is not None:
        sets.append("reminder_enabled = :enabled")
        params["enabled"] = 1 if enabled else 0

    if time_hhmm:
        sets.append("reminder_time = :t")
        params["t"] = time_hhmm

    if day_value is not None:
        if frequency == "weekly":
            # SET column takes token 'mon'..'sun'; also clear monthly JSON.
            sets.append("reminder_weekdays = :wday")
            sets.append("reminder_month_days = NULL")
            params["wday"] = _idx_to_dayname(day_value)
        else:
            # JSON column for monthly; also clear weekly SET.
            dom = max(1, min(31, int(day_value)))
            sets.append("reminder_month_days = JSON_ARRAY(:dom)")
            sets.append("reminder_weekdays = NULL")
            params["dom"] = dom

    if not sets:
        return

    sql = f"""
        UPDATE reminder_rules
           SET {", ".join(sets)}
         WHERE user_id=:uid AND is_global=1
           AND reminder_type='statements'
           AND reminder_frequency=:freq
         LIMIT 1
    """
    db.execute(text(sql), params)
    db.commit()

# --------------------------- exclusions -------------------------

def list_global_exclusions(db: Session, user_id: int, frequency: Freq) -> List[Dict[str, Any]]:
    rows = db.execute(
        text("""
            SELECT e.customer_id, c.name AS customer_name, e.created_at
              FROM reminder_global_exclusions e
              LEFT JOIN customers c ON c.id = e.customer_id
             WHERE e.user_id=:uid AND e.frequency=:freq
             ORDER BY c.name, e.customer_id
        """),
        {"uid": user_id, "freq": frequency},
    ).mappings().all()
    return [dict(r) for r in rows]

def add_global_exclusion(db: Session, user_id: int, frequency: Freq, customer_id: int) -> None:
    db.execute(
        text("""
            INSERT INTO reminder_global_exclusions (user_id, frequency, customer_id)
            VALUES (:uid, :freq, :cid)
            ON DUPLICATE KEY UPDATE created_at = created_at
        """),
        {"uid": user_id, "freq": frequency, "cid": customer_id},
    )
    db.commit()

def remove_global_exclusion(db: Session, user_id: int, frequency: Freq, customer_id: int) -> None:
    db.execute(
        text("""
            DELETE FROM reminder_global_exclusions
             WHERE user_id=:uid AND frequency=:freq AND customer_id=:cid
        """),
        {"uid": user_id, "freq": frequency, "cid": customer_id},
    )
    db.commit()

# ----------------------------- schemas --------------------------

class GlobalsOut(BaseModel):
    weekly_enabled: bool
    weekly_hour: int          # 0..23
    weekly_dow: int           # 0..6  (Mon=0)
    monthly_enabled: bool
    monthly_hour: int         # 0..23
    monthly_dom: int          # 1..31

class UpdateWeeklyIn(BaseModel):
    enabled: bool
    hour: int
    dow: int                  # 0..6 (Mon=0)

class UpdateMonthlyIn(BaseModel):
    enabled: bool
    hour: int
    dom: int                  # 1..31

class ExclusionIn(BaseModel):
    frequency: Freq
    customer_id: int

# ------------------------------ routes --------------------------

@router.get("", response_model=GlobalsOut)
def get_globals(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    ensure_global_rules(db, user.id)

    wk = get_global_rule(db, user.id, "weekly")
    mo = get_global_rule(db, user.id, "monthly")
    if not wk or not mo:
        raise HTTPException(500, "Global rules not found")

    wd = _set_to_idx(wk.get("reminder_weekdays"), 0)
    md = _json_first_int(mo.get("reminder_month_days"), 1)

    return GlobalsOut(
        weekly_enabled = bool(wk.get("reminder_enabled")),
        weekly_hour    = _hour_from_hhmm(wk.get("reminder_time")),
        weekly_dow     = max(0, min(6, wd)),
        monthly_enabled= bool(mo.get("reminder_enabled")),
        monthly_hour   = _hour_from_hhmm(mo.get("reminder_time")),
        monthly_dom    = max(1, min(31, md)),
    )

@router.post("/weekly")
def update_weekly(
    body: UpdateWeeklyIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    hhmm = f"{max(0, min(23, int(body.hour))):02d}:00"
    update_global_rule(
        db, user.id, "weekly",
        enabled=bool(body.enabled),
        time_hhmm=hhmm,
        day_value=max(0, min(6, int(body.dow))),
    )
    _recompute_next_run_for_global(db, user.id, "weekly")
    return {"ok": True}

@router.post("/monthly")
def update_monthly(
    body: UpdateMonthlyIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    hhmm = f"{max(0, min(23, int(body.hour))):02d}:00"
    update_global_rule(
        db, user.id, "monthly",
        enabled=bool(body.enabled),
        time_hhmm=hhmm,
        day_value=max(1, min(31, int(body.dom))),
    )
    _recompute_next_run_for_global(db, user.id, "monthly")
    return {"ok": True}

@router.get("/exclusions")
def list_exclusions_route(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    weekly = list_global_exclusions(db, user.id, "weekly")
    monthly = list_global_exclusions(db, user.id, "monthly")
    return (
        [{"customer_id": r["customer_id"], "customer_name": r.get("customer_name"), "frequency": "weekly"} for r in weekly]
        + [{"customer_id": r["customer_id"], "customer_name": r.get("customer_name"), "frequency": "monthly"} for r in monthly]
    )

@router.post("/exclusions")
def add_exclusion_route(
    body: ExclusionIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    if body.frequency not in ("weekly", "monthly"):
        raise HTTPException(400, "frequency must be weekly or monthly")
    add_global_exclusion(db, user.id, body.frequency, body.customer_id)
    return {"ok": True}

@router.delete("/exclusions/{frequency}/{customer_id}")
def remove_exclusion_route(
    frequency: Freq,
    customer_id: int,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    remove_global_exclusion(db, user.id, frequency, customer_id)
    return {"ok": True}

@router.get("/_debug")
def debug_globals_raw(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    rows = db.execute(text("""
        SELECT id, user_id, name, reminder_type, reminder_frequency,
               reminder_time, reminder_weekdays, reminder_month_days,
               reminder_enabled, is_global, created_at
          FROM reminder_rules
         WHERE user_id=:uid AND is_global=1 AND reminder_type='statements'
         ORDER BY reminder_frequency
    """), {"uid": user.id}).mappings().all()
    return {"rows": [dict(r) for r in rows]}

@router.get("/_trace")
def _trace(db: Session = Depends(get_db), user = Depends(require_user)):
    try:
        ensure_global_rules(db, user.id)
        wk = get_global_rule(db, user.id, "weekly")
        mo = get_global_rule(db, user.id, "monthly")
        return {"wk": wk, "mo": mo}
    except Exception:
        return PlainTextResponse(traceback.format_exc(), status_code=500)
