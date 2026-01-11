# app/routers/statement_reminders.py

from datetime import datetime, time as dtime, timedelta, timezone
import logging, json
log = logging.getLogger(__name__)
from typing import Optional, List, Literal
import traceback

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator, EmailStr, constr
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from zoneinfo import ZoneInfo
from fastapi.responses import PlainTextResponse

from sqlalchemy import func, distinct, literal
from sqlalchemy import text as sqltext

from ..database import get_db
from ..models import (
    ReminderRule,
    Customer,
    Invoice,
    StatementRun,
    EmailOutbox,
    ReminderEvent,
    User,
    AppSettings, 
)
from .auth import require_user

router = APIRouter(prefix="/api/statement_reminders", tags=["statement_reminders"])

# -------------------- Pydantic (Statements) --------------------

StatementFrequency = Literal["weekly", "monthly"]

class StatementRuleIn(BaseModel):
    name: str = Field(..., max_length=100)
    reminder_frequency: StatementFrequency = "weekly"
    reminder_time: str = "14:00"              # 'HH:MM'
    reminder_days: Optional[List[int]] = None # weekly: [0..6], monthly: [1..31]
    reminder_enabled: bool = True

    @validator("reminder_time")
    def _hhmm(cls, v):
        if not isinstance(v, str) or len(v) not in (4, 5) or ":" not in v:
            raise ValueError("reminder_time must be 'HH:MM'")
        hh, mm = v.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("reminder_time must be 'HH:MM'")
        h, m = int(hh), int(mm)
        if h < 0 or h > 23 or m < 0 or m > 59:
            raise ValueError("invalid HH:MM")
        return f"{h:02d}:{m:02d}"

class StatementRuleOut(BaseModel):
    id: int
    name: str
    reminder_frequency: StatementFrequency
    reminder_time: str                      # already normalized for output
    reminder_days: Optional[List[int]] = None
    reminder_enabled: bool
    reminder_type: Literal["statement"] = "statement"
    reminder_next_run: Optional[str] = None
    reminder_last_run: Optional[str] = None
    created_at: Optional[str] = None
    runs_count: int = 0
    emails_count: int = 0


class OneOffStatementIn(BaseModel):
    customer_id: int
    to_email: EmailStr
    subject: str = Field(..., min_length=1, max_length=255)
    message: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    statement_url: Optional[str] = None

    @validator("subject", pre=True)
    def _subject_strip_and_require(cls, v):
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("subject required")
        return v

# -------------------- Helpers --------------------

IDX2WD = {0:"mon",1:"tue",2:"wed",3:"thu",4:"fri",5:"sat",6:"sun"}
WD2IDX = {v:k for k,v in IDX2WD.items()}

def idxs_to_set(indices: Optional[List[int]]) -> Optional[str]:
    if not indices: return None
    parts = [IDX2WD[i] for i in indices if i in IDX2WD]
    return ",".join(parts) if parts else None

def set_to_idxs(s: Optional[str]) -> Optional[List[int]]:
    if not s: return None
    parts = [p.strip() for p in str(s).split(",") if p.strip()]
    out = [WD2IDX[p] for p in parts if p in WD2IDX]
    return out or None

def _parse_hhmm(s: str) -> dtime:
    h, m = map(int, s.split(":"))
    return dtime(hour=h, minute=m)


def _from_json_list(s: Optional[object]) -> Optional[List[int]]:
    if s is None:
        return None
    if isinstance(s, list):
        return [int(x) for x in s if isinstance(x, (int, str)) and str(x).isdigit()]
    if isinstance(s, str):
        try:
            v = json.loads(s)
            return v if isinstance(v, list) else None
        except Exception:
            return None
    return None

def _norm_time(v) -> str:
    # TIME from MySQL can arrive as time() or "HH:MM:SS"
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%H:%M")
        except Exception:
            pass
    s = str(v)
    parts = s.split(":")
    if len(parts) >= 2:
        return f"{parts[0]:0>2}:{parts[1]:0>2}"
    return s[:5]


def _iso_utc(dt: datetime | None) -> str | None:
    """Return RFC3339-style UTC string with trailing 'Z' (or None)."""
    if not dt:
        return None
    # Attach UTC if naive (we store in UTC), else convert to UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def _to_out_statement(rule: ReminderRule) -> StatementRuleOut:
    days_out = (
        set_to_idxs(rule.reminder_weekdays)
        if rule.reminder_frequency == "weekly"
        else _from_json_list(rule.reminder_month_days)
    )
    return StatementRuleOut(
        id=rule.id,
        name=rule.name,
        reminder_frequency=rule.reminder_frequency,   # weekly | monthly
        reminder_time=_norm_time(rule.reminder_time), # still 'HH:MM'
        reminder_days=days_out,
        reminder_enabled=bool(rule.reminder_enabled),
        reminder_type="statement",
        reminder_next_run=_iso_utc(rule.reminder_next_run_utc),
        reminder_last_run=_iso_utc(rule.reminder_last_run_utc),
        created_at=_iso_utc(rule.created_at),
    )

class PreviewOut(BaseModel):
    rule_id: int
    next_runs: List[str]

# Helper to calculate runs and email count
def _rules_with_counts(db: Session, user_id: int):
    # runs per rule
    runs_sq = (
        db.query(
            StatementRun.rule_id.label("rule_id"),
            func.count(StatementRun.id).label("runs_count"),
        )
        .group_by(StatementRun.rule_id)
        .subquery()
    )

    # emails per rule
    emails_sq = (
        db.query(
            EmailOutbox.rule_id.label("rule_id"),
            func.count(EmailOutbox.id).label("emails_count"),
        )
        .group_by(EmailOutbox.rule_id)
        .subquery()
    )

    return (
        db.query(
            ReminderRule,
            func.coalesce(runs_sq.c.runs_count, literal(0)).label("runs_count"),
            func.coalesce(emails_sq.c.emails_count, literal(0)).label("emails_count"),
        )
        .filter(
            ReminderRule.user_id == user_id,
            ReminderRule.reminder_type == "statements",
        )
        .outerjoin(runs_sq, runs_sq.c.rule_id == ReminderRule.id)
        .outerjoin(emails_sq, emails_sq.c.rule_id == ReminderRule.id)
        .order_by(ReminderRule.created_at.desc(), ReminderRule.id.desc())
    ).all()

# ---------helper to filter out eligible customers for global statement rules---------

def _eligible_customers_for_rule(db: Session, user_id: int, rule) -> list:
    """
    Returns customers eligible for this rule.
    - For global rules, excludes any listed in reminder_global_exclusions for this frequency.
    - Always requires a non-empty email.
    TODO (optional): add "has open invoices" or other eligibility filters here.
    """
    params = {"uid": user_id}
    if getattr(rule, "is_global", 0):
        params["freq"] = rule.reminder_frequency
        sql = sqltext("""
            SELECT c.id, c.name, c.email
              FROM customers c
         LEFT JOIN reminder_global_exclusions e
                ON e.user_id = :uid
               AND e.frequency = :freq
               AND e.customer_id = c.id
             WHERE c.user_id = :uid
               AND e.customer_id IS NULL
               AND c.email IS NOT NULL
               AND TRIM(c.email) <> ''
        """)
    else:
        sql = sqltext("""
            SELECT c.id, c.name, c.email
              FROM customers c
             WHERE c.user_id = :uid
               AND c.email IS NOT NULL
               AND TRIM(c.email) <> ''
        """)

    rows = db.execute(sql, params).fetchall()
    # Return as simple objects/dicts to match later usage
    return [{"id": r.id, "name": r.name, "email": r.email} for r in rows]


# --- Timezone helpers ---------------------------------------------------------

def _get_user_tz(db: Session, user_id: int) -> ZoneInfo:
    """Return user's IANA timezone (defaults to UTC)."""
    row = db.query(AppSettings).filter(AppSettings.user_id == user_id).first()
    tz_name = getattr(row, "timezone", None) or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")

def _local_hhmm_next_utc(freq: str, hhmm: str, days: Optional[List[int]], tz: ZoneInfo) -> datetime:
    """
    Compute the next run (local to user's tz) then return it as *naive UTC*,
    which is what reminder_next_run_utc stores.
    """
    now_tz = datetime.now(tz)

    hh, mm = map(int, hhmm.split(":"))
    today_local = now_tz.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def to_utc_naive(dt_local: datetime) -> datetime:
        return dt_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    if freq == "weekly":
        if not days:
            cand = today_local if today_local > now_tz else (today_local + timedelta(days=1))
            return to_utc_naive(cand)
        for offset in range(0, 8):
            d = now_tz + timedelta(days=offset)
            if d.weekday() in days:
                cand = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if cand > now_tz:
                    return to_utc_naive(cand)
        cand = today_local + timedelta(days=1)
        return to_utc_naive(cand)

    if freq == "monthly":
        from calendar import monthrange
        want = (days or [1])[0]

        def build(y, m, d):
            d = min(d, monthrange(y, m)[1])
            return datetime(y, m, d, hh, mm, tzinfo=tz)

        cand = build(now_tz.year, now_tz.month, want)
        if cand > now_tz:
            return to_utc_naive(cand)
        y, m = (now_tz.year + 1, 1) if now_tz.month == 12 else (now_tz.year, now_tz.month + 1)
        return to_utc_naive(build(y, m, want))

    cand = today_local if today_local > now_tz else (today_local + timedelta(days=1))
    return to_utc_naive(cand)


# -------------------- Statements CRUD --------------------

@router.get("/statements", response_model=List[StatementRuleOut])
def list_statement_rules(db: Session = Depends(get_db), user=Depends(require_user)):
    try:
        rows = _rules_with_counts(db, user.id)
        out: List[StatementRuleOut] = []
        for rule, runs_cnt, emails_cnt in rows:
            dto = _to_out_statement(rule)
            dto.runs_count = int(runs_cnt or 0)
            dto.emails_count = int(emails_cnt or 0)
            out.append(dto)
        return out
    except Exception:
        tb = traceback.format_exc()
        log.error("list_statement_rules failed:\n%s", tb)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(tb, status_code=500)


@router.post("/statements", response_model=StatementRuleOut)
def create_statement_rule(payload: StatementRuleIn, db: Session = Depends(get_db), user=Depends(require_user)):
    if payload.reminder_frequency not in ("weekly", "monthly"):
        raise HTTPException(400, "Statements can only be scheduled weekly or monthly.")

    tz = _get_user_tz(db, user.id)
    next_run = _local_hhmm_next_utc(payload.reminder_frequency, payload.reminder_time, payload.reminder_days, tz)
    r = ReminderRule(
        user_id=user.id,
        name=payload.name,
        reminder_type="statements",                     # statements only
        reminder_sequence_id=None,
        reminder_frequency=payload.reminder_frequency,  # weekly|monthly
        reminder_time=payload.reminder_time,
        reminder_timezone=None,
        reminder_weekdays=idxs_to_set(payload.reminder_days) if payload.reminder_frequency == "weekly" else None,
        reminder_month_days=json.dumps(payload.reminder_days) if (payload.reminder_frequency == "monthly" and payload.reminder_days) else None,
        reminder_invoice_filter="all",                  # statements always 'all'
        reminder_enabled=payload.reminder_enabled,
        reminder_next_run_utc=next_run,
        reminder_last_run_utc=None,
        schedule="",    # NOT NULL in table
        escalate=0,     # NOT NULL in table
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _to_out_statement(r)

@router.patch("/statements/{rule_id}", response_model=StatementRuleOut)
def update_statement_rule(rule_id: int, payload: StatementRuleIn, db: Session = Depends(get_db), user=Depends(require_user)):
    r = (
        db.query(ReminderRule)
          .filter(ReminderRule.id == rule_id,
                  ReminderRule.user_id == user.id,
                  ReminderRule.reminder_type == "statements")
          .first()
    )
    if not r:
        raise HTTPException(404, "Rule not found")

    if payload.reminder_frequency not in ("weekly", "monthly"):
        raise HTTPException(400, "Statements can only be scheduled weekly or monthly.")

    # update fields
    r.name = payload.name
    r.reminder_frequency = payload.reminder_frequency
    r.reminder_time = payload.reminder_time
    r.reminder_invoice_filter = "all"
    r.reminder_enabled = payload.reminder_enabled

    if payload.reminder_frequency == "weekly":
        r.reminder_weekdays = idxs_to_set(payload.reminder_days)
        r.reminder_month_days = None
    else:
        r.reminder_month_days = json.dumps(payload.reminder_days) if payload.reminder_days else None
        r.reminder_weekdays = None

    # compute next run from user's timezone, store as UTC (naive)
    tz = _get_user_tz(db, user.id)
    r.reminder_next_run_utc = _local_hhmm_next_utc(
        payload.reminder_frequency,
        payload.reminder_time,
        payload.reminder_days,
        tz,
    )

    r.schedule = r.schedule or ""
    r.escalate = 0 if r.escalate is None else r.escalate

    db.commit()
    db.refresh(r)
    return _to_out_statement(r)


@router.delete("/statements/{rule_id}")
def delete_statement_rule(rule_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    # Check counts first
    row = (
        db.query(
            func.count(StatementRun.id),
            func.count(EmailOutbox.id),
        )
        .select_from(ReminderRule)
        .outerjoin(StatementRun, StatementRun.rule_id == ReminderRule.id)
        .outerjoin(EmailOutbox, EmailOutbox.rule_id == ReminderRule.id)
        .filter(ReminderRule.id == rule_id,
                ReminderRule.user_id == user.id,
                ReminderRule.reminder_type == "statements")
        .first()
    )
    if not row:
        raise HTTPException(404, "Rule not found")

    runs_cnt, emails_cnt = (int(row[0] or 0), int(row[1] or 0))
    if runs_cnt > 0 or emails_cnt > 0:
        # refuse hard delete if there is activity
        raise HTTPException(
            status_code=409,
            detail="This rule has history and can not be deleted. Disable it instead.",
        )

    # Safe to hard delete
    n = (
        db.query(ReminderRule)
          .filter(ReminderRule.id == rule_id,
                  ReminderRule.user_id == user.id,
                  ReminderRule.reminder_type == "statements")
          .delete()
    )
    db.commit()
    if not n:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


@router.get("/statements/{rule_id}/preview", response_model=PreviewOut)
def preview_statement_rule(rule_id: int, days: int = 14, db: Session = Depends(get_db), user=Depends(require_user)):
    r = (
        db.query(ReminderRule)
          .filter(ReminderRule.id == rule_id, ReminderRule.user_id == user.id, ReminderRule.reminder_type == "statements")
          .first()
    )
    if not r:
        raise HTTPException(404, "Rule not found")

    days_list = set_to_idxs(r.reminder_weekdays) if r.reminder_frequency == "weekly" else _from_json_list(r.reminder_month_days)

    runs: List[str] = []
    tz = _get_user_tz(db, user.id)
    cur = r.reminder_next_run_utc or _local_hhmm_next_utc(r.reminder_frequency, _norm_time(r.reminder_time), days_list, tz)

    end = datetime.utcnow() + timedelta(days=max(1, min(days, 90)))

    def step_once(cur: datetime) -> datetime:
        if r.reminder_frequency == "weekly":
            if not days_list:
                return cur + timedelta(days=7)
            for _ in range(8):
                cur = cur + timedelta(days=1)
                if cur.weekday() in days_list:
                    return cur
            return cur
        # monthly
        from calendar import monthrange
        want = (days_list or [1])[0]
        y, m = (cur.year, cur.month + 1) if cur.month < 12 else (cur.year + 1, 1)
        dmax = monthrange(y, m)[1]
        d = min(want, dmax)
        return datetime(y, m, d, cur.hour, cur.minute)

    while cur <= end and len(runs) < 30:
        runs.append(cur.isoformat())
        cur = step_once(cur)

    return PreviewOut(rule_id=r.id, next_runs=runs)

# -------------------- Enqueue-due (scalable, idempotent) --------------------

def _customers_with_email(db: Session, user_id: int):
    return (
        db.query(Customer)
          .filter(Customer.user_id == user_id,
                  Customer.email.isnot(None),
                  Customer.email != "")
          .order_by(Customer.id.asc())
          .all()
    )

def _statement_subject(customer_name: str):
    return f"Statement for {customer_name}"

def _statement_body(default_message: str = None):
    return (default_message or "Please find your latest statement below.\n\nRegards,\nAccounts")

@router.post("/email/statement/enqueue-one")
def enqueue_one_statement(body: OneOffStatementIn, db: Session = Depends(get_db), user=Depends(require_user)):
    # 1) sanity + ownership
    cust = (
        db.query(Customer)
          .filter(Customer.id == body.customer_id, Customer.user_id == user.id)
          .first()
    )
    if not cust:
        raise HTTPException(404, "Customer not found")

    now = datetime.utcnow()

    # 2) build payload (force to JSON string in case the DB column isn't true JSON)
    payload_dict = {
        "customer_id": body.customer_id,
        "date_from": body.date_from,
        "date_to": body.date_to,
        "statement_url": body.statement_url or f"/customers/{body.customer_id}/statement",
        "one_off": True,
    }
    try:
        payload_json = json.dumps(payload_dict)
    except Exception:
        payload_json = None  # last resort

    # 3) create outbox job
    job = EmailOutbox(
        user_id=user.id,
        customer_id=body.customer_id,
        channel="email",
        template="statement",
        to_email=str(body.to_email),
        subject=body.subject.strip(),
        body=body.message or "",
        payload_json=payload_json,  # works whether column is JSON or TEXT
        rule_id=None,
        run_id=None,
        status="queued",
        next_attempt_at=now,
    )

    try:
        db.add(job)
        db.commit()
        db.refresh(job)
        return {"ok": True, "job_id": int(job.id)}
    except Exception as e:
        db.rollback()
        # log full details to server console AND return a debuggable error
        log.exception("enqueue-one failed user=%s customer=%s", user.id, body.customer_id)
        raise HTTPException(status_code=500, detail=f"enqueue-one failed: {type(e).__name__}: {e}")

@router.post("/statements/enqueue-due")
def enqueue_due_statement_runs(db: Session = Depends(get_db)):
    try:
        now = datetime.utcnow()
        rules = (
            db.query(ReminderRule)
              .filter(
                  ReminderRule.reminder_type == "statements",
                  ReminderRule.reminder_enabled == True,                     # noqa: E712
                  ReminderRule.reminder_next_run_utc.isnot(None),
                  ReminderRule.reminder_next_run_utc <= now,
              )
              .order_by(ReminderRule.reminder_next_run_utc.asc())
              # .with_for_update(skip_locked=True)
              .all()
        )

        processed = []
        for rule in rules:
            user = db.query(User).filter(User.id == rule.user_id).first()
            if not user:
                continue

            run = StatementRun(
                rule_id=rule.id,
                user_id=user.id,
                run_scheduled_at=rule.reminder_next_run_utc,
                status="queued",
                created_at=now,
            )
            try:
                with db.begin_nested():
                    db.add(run)
                    db.flush()
            except Exception:
                db.rollback()
                run = (db.query(StatementRun)
                         .filter(StatementRun.rule_id == rule.id,
                                 StatementRun.run_scheduled_at == rule.reminder_next_run_utc)
                         .first())
                if not run:
                    raise

            # ⬇️ use the new helper (applies global exclusions if is_global=1)
            customers = _eligible_customers_for_rule(db, user_id=user.id, rule=rule)
            run.total_customers = len(customers)

            jobs = 0
            for c in customers:
                job = EmailOutbox(
                    user_id=user.id,
                    customer_id=c["id"],
                    channel="email",
                    template="statement",
                    to_email=c["email"],
                    subject=_statement_subject(c["name"]),
                    body=_statement_body(),
                    payload_json={
                        "statement_url": f"/customers/{c['id']}/statement",
                        "customer_id": c["id"],
                        "rule_id": rule.id,
                        "run_id": run.id,
                    },
                    rule_id=rule.id,
                    run_id=run.id,
                    status="queued",
                    next_attempt_at=now,
                )
                try:
                    with db.begin_nested():
                        db.add(job)
                        db.flush()
                    jobs += 1
                except IntegrityError:
                    db.rollback()

            run.jobs_enqueued = jobs
            processed.append({"rule_id": rule.id, "run_id": run.id, "jobs": jobs})

            # advance next_run (unchanged)
            days_list = (set_to_idxs(rule.reminder_weekdays)
                         if rule.reminder_frequency == "weekly"
                         else _from_json_list(rule.reminder_month_days))
            tz = _get_user_tz(db, rule.user_id)
            rule.reminder_last_run_utc = now
            rule.reminder_next_run_utc = _local_hhmm_next_utc(
                rule.reminder_frequency,
                _norm_time(rule.reminder_time),
                days_list,
                tz,
            )

        db.commit()
        return {"ok": True, "runs": processed}
    except Exception as e:
        db.rollback()
        tb = traceback.format_exc()
        log.error("[enqueue-due] %s\n%s", e, tb)
        return PlainTextResponse(f"enqueue-due failed: {type(e).__name__}: {e}\n\n{tb}", status_code=500)
