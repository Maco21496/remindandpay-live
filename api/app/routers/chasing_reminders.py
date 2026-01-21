# app/routers/chasing_reminders.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone, date
from typing import Optional, List, Literal, Dict
import json
import traceback
from decimal import Decimal

from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy import func, select, text as sqltext

from ..database import get_db
from .auth import require_user
from ..models import (
    ReminderRule,      # scheduling rule for chasing
    ChasingPlan,       # was ReminderSequence
    ChasingTrigger,    # was ReminderStep
    ReminderTemplate,
    AccountSmsSettings,
    Customer,
    Invoice,
    EmailOutbox,
    ReminderEvent,
    AppSettings,
)

router = APIRouter(prefix="/api/chasing_reminders", tags=["chasing_reminders"])

# ---------- Schemas ----------

class ChasingRuleIn(BaseModel):
    name: str = Field(..., max_length=100)
    reminder_time: str = "14:00"           # HH:MM local time
    reminder_enabled: bool = True
    default_sequence_id: Optional[int] = None  # fallback chasing_plan.id if customer has no override

    @validator("reminder_time")
    def _hhmm(cls, v: str) -> str:
        if not isinstance(v, str) or ":" not in v:
            raise ValueError("reminder_time must be 'HH:MM'")
        hh, mm = v.split(":")[:2]
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("reminder_time must be 'HH:MM'")
        h, m = int(hh), int(mm)
        if h < 0 or h > 23 or m < 0 or m > 59:
            raise ValueError("invalid HH:MM")
        return f"{h:02d}:{m:02d}"

class ChasingRuleOut(BaseModel):
    id: int
    name: str
    reminder_time: str
    reminder_enabled: bool
    reminder_type: Literal["chasing"] = "chasing"
    default_sequence_id: Optional[int] = None
    reminder_next_run: Optional[str] = None
    reminder_last_run: Optional[str] = None
    created_at: Optional[str] = None
    runs_count: int = 0
    emails_count: int = 0

class SendNowIn(BaseModel):
    sequence_id: Optional[int] = None             # optional override plan
    customer_ids: Optional[List[int]] = None      # optional subset of customers
    limit: Optional[int] = Field(default=None, ge=1)
    ignore_dedupe_hours: Optional[int] = Field(default=None, ge=0)
    delivery_mode: Optional[str] = Field(default=None, description="email|sms|both")

class SendNowOut(BaseModel):
    ok: bool
    jobs: int
    targeted_customers: int

# ---------- Helpers ----------

def _norm_time(v) -> str:
    if hasattr(v, "strftime"):
        return v.strftime("%H:%M")
    s = str(v)
    parts = s.split(":")
    return f"{parts[0]:0>2}:{parts[1]:0>2}"

def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def _user_tz(db: Session, user_id: int):
    from zoneinfo import ZoneInfo
    row = db.query(AppSettings).filter(AppSettings.user_id == user_id).first()
    tz = getattr(row, "timezone", None) or "UTC"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")

def _next_local_daily_utc(hhmm: str, tz) -> datetime:
    now = datetime.now(tz)
    hh, mm = map(int, hhmm.split(":"))
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= now:
        cand = cand + timedelta(days=1)
    # store naive UTC in DB (matches existing behaviour)
    return cand.astimezone(timezone.utc).replace(tzinfo=None)

def _rule_out(r: ReminderRule) -> ChasingRuleOut:
    return ChasingRuleOut(
        id=r.id,
        name=r.name,
        reminder_time=_norm_time(r.reminder_time),
        reminder_enabled=bool(r.reminder_enabled),
        default_sequence_id=getattr(r, "reminder_sequence_id", None),
        reminder_next_run=_iso_utc(r.reminder_next_run_utc),
        reminder_last_run=_iso_utc(r.reminder_last_run_utc),
        created_at=_iso_utc(r.created_at),
        runs_count=0,
        emails_count=0,
    )

def _oldest_overdue_invoice_id(db: Session, user_id: int, customer_id: int) -> Optional[int]:
    sql = sqltext("""
        SELECT i.id
          FROM invoices i
          LEFT JOIN (
                SELECT pa.invoice_id, COALESCE(SUM(pa.amount),0) AS alloc_sum
                  FROM payment_allocations pa
                  JOIN payments p ON p.id = pa.payment_id
                 WHERE p.customer_id = :cid
                 GROUP BY pa.invoice_id
          ) a ON a.invoice_id = i.id
         WHERE i.user_id = :uid
           AND i.customer_id = :cid
           AND i.kind = 'invoice'
           AND i.due_date IS NOT NULL
           AND i.due_date < CURRENT_DATE()
           AND (i.amount_due - COALESCE(a.alloc_sum,0)) > 0.005
         ORDER BY i.due_date ASC, i.id ASC
         LIMIT 1
    """)
    return db.execute(sql, {"uid": user_id, "cid": customer_id}).scalar()

# all eligible chasing targets = not excluded in reminder_global_exclusions for 'chasing'
def _eligible_customers(db: Session, user_id: int) -> list[Dict]:
    sql = sqltext("""
        SELECT c.id, c.name, c.email, c.phone, c.reminder_sequence_id
          FROM customers c
     LEFT JOIN reminder_global_exclusions e
            ON e.user_id = :uid
           AND e.frequency = 'chasing'
           AND e.customer_id = c.id
         WHERE c.user_id = :uid
           AND e.customer_id IS NULL
    """)
    rows = db.execute(sql, {"uid": user_id}).fetchall()
    return [
        {
            "id": r.id,
            "name": r.name,
            "email": r.email,
            "phone": r.phone,
            "seq_id": r.reminder_sequence_id,
        }
        for r in rows
    ]

def _oldest_days_overdue(db: Session, user_id: int, customer_id: int) -> int:
    sql = sqltext("""
        SELECT MAX(DATEDIFF(CURRENT_DATE(), i.due_date)) AS days
          FROM invoices i
          LEFT JOIN (
                SELECT pa.invoice_id, COALESCE(SUM(pa.amount),0) AS alloc_sum
                  FROM payment_allocations pa
                  JOIN payments p ON p.id = pa.payment_id
                 WHERE p.customer_id = :cid
                 GROUP BY pa.invoice_id
          ) a ON a.invoice_id = i.id
         WHERE i.user_id = :uid
           AND i.customer_id = :cid
           AND i.kind = 'invoice'
           AND i.due_date IS NOT NULL
           AND i.due_date < CURRENT_DATE()
           AND (i.amount_due - COALESCE(a.alloc_sum,0)) > 0.005
    """)
    v = db.execute(sql, {"uid": user_id, "cid": customer_id}).scalar()
    return int(v or 0)

def _choose_step(
    db: Session,
    seq_id: int,
    days_overdue: int,
    channel: Optional[str] = None,
) -> Optional[ChasingTrigger]:
    # pick the most "aggressive" trigger whose offset_days <= how overdue they are
    query = (
        db.query(ChasingTrigger)
          .filter(ChasingTrigger.sequence_id == seq_id)
          .filter(ChasingTrigger.offset_days <= days_overdue)
    )
    if channel:
        query = query.filter(ChasingTrigger.channel == channel)
    return query.order_by(
        ChasingTrigger.offset_days.desc(),
        ChasingTrigger.id.asc(),
    ).first()

def _load_template(db: Session, user_id: int, key: str, channel: str = "email") -> Optional[ReminderTemplate]:
    return (
        db.query(ReminderTemplate)
          .filter(
              ReminderTemplate.user_id == user_id,
              ReminderTemplate.key == key,
              ReminderTemplate.channel == channel,
              ReminderTemplate.is_active == True,   # noqa: E712
          )
          .first()
    )

# simple dedupe: don’t enqueue same (customer, template_key) recently
def _sent_recently(
    db: Session,
    user_id: int,
    customer_id: int,
    template_key: str,
    channel: Optional[str] = None,
) -> bool:
    since = datetime.utcnow() - timedelta(hours=1)
    query = (
        db.query(EmailOutbox.id)
          .filter(
              EmailOutbox.user_id == user_id,
              EmailOutbox.customer_id == customer_id,
              EmailOutbox.template == template_key,
              EmailOutbox.created_at >= since,
          )
    )
    if channel:
        query = query.filter(EmailOutbox.channel == channel)
    cnt = query.count()
    return cnt > 0

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

def _get_sms_settings(db: Session, user_id: int) -> tuple[bool, str]:
    row = _ensure_sms_settings(db, user_id)
    enabled = bool(getattr(row, "enabled", False))
    mode = (getattr(row, "chasing_delivery_mode", None) or "email").lower()
    if mode not in ("email", "sms", "both"):
        mode = "email"
    return enabled, mode

def _allowed_channels(delivery_mode: str, sms_enabled: bool) -> list[str]:
    if delivery_mode == "both":
        channels = ["email", "sms"]
    elif delivery_mode == "sms":
        channels = ["sms"]
    else:
        channels = ["email"]
    if not sms_enabled and "sms" in channels:
        channels = [c for c in channels if c != "sms"]
    return channels

def _customer_overdue_summary(db: Session, user_id: int, customer_id: int) -> dict:
    cust = (
        db.query(Customer)
          .filter(Customer.user_id == user_id, Customer.id == customer_id)
          .first()
    )
    customer_name = getattr(cust, "name", "") or "Customer"

    # list overdue invoices (top 10)
    sql_rows = sqltext("""
        SELECT
            i.id,
            i.invoice_number,
            DATE_FORMAT(i.due_date, '%Y-%m-%d') AS due_date,
            (i.amount_due - COALESCE(a.alloc_sum,0)) AS outstanding,
            DATEDIFF(CURRENT_DATE(), i.due_date) AS days_overdue
          FROM invoices i
          LEFT JOIN (
                SELECT pa.invoice_id, COALESCE(SUM(pa.amount),0) AS alloc_sum
                  FROM payment_allocations pa
                  JOIN payments p ON p.id = pa.payment_id
                 WHERE p.customer_id = :cid
                 GROUP BY pa.invoice_id
          ) a ON a.invoice_id = i.id
         WHERE i.user_id = :uid
           AND i.customer_id = :cid
           AND i.kind = 'invoice'
           AND i.due_date IS NOT NULL
           AND i.due_date < CURRENT_DATE()
           AND (i.amount_due - COALESCE(a.alloc_sum,0)) > 0.005
         ORDER BY i.due_date ASC, i.id ASC
         LIMIT 10
    """)
    rows = [dict(r) for r in db.execute(sql_rows, {"uid": user_id, "cid": customer_id}).mappings().all()]

    # totals
    sql_tot = sqltext("""
        SELECT
            COUNT(*) AS cnt,
            COALESCE(SUM(i.amount_due - COALESCE(a.alloc_sum,0)), 0) AS total
          FROM invoices i
          LEFT JOIN (
                SELECT pa.invoice_id, COALESCE(SUM(pa.amount),0) AS alloc_sum
                  FROM payment_allocations pa
                  JOIN payments p ON p.id = pa.payment_id
                 WHERE p.customer_id = :cid
                 GROUP BY pa.invoice_id
          ) a ON a.invoice_id = i.id
         WHERE i.user_id = :uid
           AND i.customer_id = :cid
           AND i.kind = 'invoice'
           AND i.due_date IS NOT NULL
           AND i.due_date < CURRENT_DATE()
           AND (i.amount_due - COALESCE(a.alloc_sum,0)) > 0.005
    """)
    tot = db.execute(sql_tot, {"uid": user_id, "cid": customer_id}).mappings().first() or {"cnt": 0, "total": 0}

    overdue_total = float(tot["total"] or 0)
    overdue_total_str = f"{Decimal(overdue_total):,.2f}"

    oldest = rows[0] if rows else None
    oldest_invoice = None
    if oldest:
        oldest_invoice = {
            "invoice_number": oldest.get("invoice_number"),
            "due_date": oldest.get("due_date"),
            "outstanding": float(oldest.get("outstanding") or 0),
            "outstanding_str": f"{Decimal(oldest.get('outstanding') or 0):,.2f}",
            "days_overdue": int(oldest.get("days_overdue") or 0),
        }

    invoices_for_table = [{
        "invoice_number": r.get("invoice_number"),
        "due_date": r.get("due_date"),
        "amount_due": r.get("outstanding"),
    } for r in rows]

    return {
        "customer_name": customer_name,
        "invoice_count": int(tot["cnt"] or 0),
        "overdue_total": overdue_total_str,
        "overdue_total_num": overdue_total,
        "oldest_days_overdue": int(oldest.get("days_overdue") or 0) if oldest else 0,
        "oldest_invoice": oldest_invoice,
        "invoices": invoices_for_table,
        "pay_url": "",
    }

def _render_tokens(text: str, ctx: dict) -> str:
    if not text:
        return ""

    # flatten dict keys (so {{ oldest_invoice.invoice_number }} works)
    flat: Dict[str, str] = {}
    def flatten(prefix: str, obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                flatten(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(obj, (list, tuple, set)):
            return
        else:
            flat[prefix] = "" if obj is None else str(obj)
    flatten("", ctx)

    aliases = {
        "invoice.invoice_number": flat.get("oldest_invoice.invoice_number", ""),
        "invoice.amount_due":     flat.get("oldest_invoice.outstanding_str", flat.get("oldest_invoice.outstanding", "")),
        "days_overdue":           flat.get("oldest_days_overdue", ""),
        "customer.name":          flat.get("customer_name", ""),
        "invoice.amount":         flat.get("oldest_invoice.outstanding_str", ""),
        "payment_link":           flat.get("pay_url", ""),
    }

    out = text
    for k, v in flat.items():
        out = out.replace(f"{{{{ {k} }}}}", v)
    for k, v in aliases.items():
        out = out.replace(f"{{{{ {k} }}}}", v)
    return out

def _invoices_table_html(invoices: list[dict]) -> str:
    if not invoices:
        return ""
    rows = []
    for inv in invoices:
        amt = f"{Decimal(inv.get('amount_due') or 0):,.2f}"
        rows.append(
            "<tr>"
              f"<td>{inv.get('invoice_number','')}</td>"
              f"<td>{inv.get('due_date','')}</td>"
              f"<td style='text-align:right'>{amt}</td>"
            "</tr>"
        )
    return (
        "<table style='border-collapse:collapse;font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:14px'>"
        "<thead><tr>"
        "<th style='text-align:left;padding-right:10px'>Invoice</th>"
        "<th style='text-align:left;padding-right:10px'>Due date</th>"
        "<th style='text-align:right'>Amount</th>"
        "</tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )

# ---------- CRUD for chasing rules ----------

@router.get("", response_model=List[ChasingRuleOut])
def list_rules(db: Session = Depends(get_db), user=Depends(require_user)):
    rows = (
        db.query(ReminderRule)
          .filter(
              ReminderRule.user_id == user.id,
              ReminderRule.reminder_type == "chasing",
          )
          .order_by(ReminderRule.created_at.desc(), ReminderRule.id.desc())
          .all()
    )
    return [_rule_out(r) for r in rows]

@router.post("", response_model=ChasingRuleOut)
def create_rule(body: ChasingRuleIn, db: Session = Depends(get_db), user=Depends(require_user)):
    if body.default_sequence_id:
        plan = (
            db.query(ChasingPlan)
              .filter(
                  ChasingPlan.id == body.default_sequence_id,
                  ChasingPlan.user_id == user.id,
              )
              .first()
        )
        if not plan:
            raise HTTPException(400, "Default chasing plan not found")

    tz = _user_tz(db, user.id)
    next_run = _next_local_daily_utc(body.reminder_time, tz)

    r = ReminderRule(
        user_id=user.id,
        name=body.name,
        reminder_type="chasing",
        reminder_sequence_id=body.default_sequence_id,
        reminder_time=body.reminder_time,
        reminder_enabled=body.reminder_enabled,
        reminder_next_run_utc=next_run,
        schedule="",
        escalate=0,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _rule_out(r)

@router.patch("/{rule_id}", response_model=ChasingRuleOut)
def update_rule(rule_id: int, body: ChasingRuleIn, db: Session = Depends(get_db), user=Depends(require_user)):
    r = (
        db.query(ReminderRule)
          .filter(
              ReminderRule.id == rule_id,
              ReminderRule.user_id == user.id,
              ReminderRule.reminder_type == "chasing",
          )
          .first()
    )
    if not r:
        raise HTTPException(404, "Rule not found")

    if body.default_sequence_id:
        plan = (
            db.query(ChasingPlan)
              .filter(
                  ChasingPlan.id == body.default_sequence_id,
                  ChasingPlan.user_id == user.id,
              )
              .first()
        )
        if not plan:
            raise HTTPException(400, "Default chasing plan not found")

    r.name = body.name
    r.reminder_time = body.reminder_time
    r.reminder_enabled = body.reminder_enabled
    r.reminder_sequence_id = body.default_sequence_id

    tz = _user_tz(db, user.id)
    r.reminder_next_run_utc = _next_local_daily_utc(body.reminder_time, tz)

    db.commit()
    db.refresh(r)
    return _rule_out(r)

@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    n = (
        db.query(ReminderRule)
          .filter(
              ReminderRule.id == rule_id,
              ReminderRule.user_id == user.id,
              ReminderRule.reminder_type == "chasing",
          )
          .delete()
    )
    db.commit()
    if not n:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}

# ---------- Manual "send now" ----------

@router.post("/send-now", response_model=SendNowOut)
def send_now(
    body: SendNowIn = Body(...),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    now = datetime.utcnow()

    # build eligible customer pool
    all_customers = _eligible_customers(db, user.id)
    if body.customer_ids:
        allowed = set(int(x) for x in body.customer_ids)
        pool = [c for c in all_customers if c["id"] in allowed]
    else:
        pool = all_customers

    if body.limit:
        pool = pool[: int(body.limit)]

    def _sent_recently_override(db_, user_id_, customer_id_, template_key_, channel_):
        if body.ignore_dedupe_hours is None:
            return _sent_recently(db_, user_id_, customer_id_, template_key_, channel_)
        since = datetime.utcnow() - timedelta(hours=body.ignore_dedupe_hours)
        query = (
            db_.query(EmailOutbox.id)
               .filter(
                   EmailOutbox.user_id == user_id_,
                   EmailOutbox.customer_id == customer_id_,
                   EmailOutbox.template == template_key_,
                   EmailOutbox.created_at >= since,
               )
        )
        if channel_:
            query = query.filter(EmailOutbox.channel == channel_)
        cnt = query.count()
        return cnt > 0

    # default chasing rule (non-global row, is_global=0)
    rule = (
        db.query(ReminderRule)
          .filter(
              ReminderRule.user_id == user.id,
              ReminderRule.reminder_type == "chasing",
              ReminderRule.is_global == 0,
          )
          .first()
    )

    sms_enabled, default_mode = _get_sms_settings(db, user.id)
    delivery_mode = (body.delivery_mode or default_mode or "email").lower()
    channels = _allowed_channels(delivery_mode, sms_enabled)

    jobs = 0
    if not channels:
        return SendNowOut(ok=True, jobs=jobs, targeted_customers=len(pool))

    for c in pool:
        sp = db.begin_nested()  # savepoint per customer
        try:
            enqueued = 0
            # pick which plan to use:
            # manual override -> customer override -> default on rule
            seq_id = body.sequence_id or c.get("seq_id") or (rule.reminder_sequence_id if rule else None)
            if not seq_id:
                sp.rollback()
                continue

            days = _oldest_days_overdue(db, user.id, c["id"])
            if days <= 0:
                sp.rollback()
                continue
            for channel in channels:
                if channel == "email":
                    to_email = (c.get("email") or "").strip()
                    if not to_email:
                        continue
                    if len(to_email) > 254:
                        to_email = to_email[:254]
                else:
                    to_email = (c.get("phone") or "").strip()
                    if not to_email:
                        continue

                trigger = _choose_step(db, seq_id, days, channel=channel)
                if not trigger:
                    continue

                tpl = _load_template(db, user.id, trigger.template_key, trigger.channel)
                if not tpl:
                    continue

                if _sent_recently_override(db, user.id, c["id"], trigger.template_key, channel):
                    continue

                subj_raw = (tpl.subject or "").strip()
                if len(subj_raw) > 255:
                    subj_raw = subj_raw[:255]

                template_key = (trigger.template_key or "").strip()
                if len(template_key) > 64:
                    template_key = template_key[:64]

                summary = _customer_overdue_summary(db, user.id, c["id"])
                ctx = {
                    "customer_name": summary["customer_name"],
                    "invoice_count": summary["invoice_count"],
                    "overdue_total": summary["overdue_total"],
                    "oldest_days_overdue": summary["oldest_days_overdue"],
                    "oldest_invoice": summary["oldest_invoice"],
                    "pay_url": summary["pay_url"],
                    "invoices_table": _invoices_table_html(summary["invoices"]),
                }

                body_html_raw = (tpl.body_html or "").strip()
                body_text_raw = (tpl.body_text or "").strip()

                subj      = _render_tokens(subj_raw,      ctx)
                body_html = _render_tokens(body_html_raw, ctx)
                body_text = _render_tokens(body_text_raw, ctx)
                body      = body_text if channel == "sms" else (body_html or body_text)
                if not body:
                    continue

                payload = {
                    "sequence_id": seq_id,
                    "step_id": trigger.id,
                    "days_overdue": days,
                    "channel": channel,
                    "summary": {
                        "invoice_count": summary["invoice_count"],
                        "overdue_total": summary["overdue_total"],
                        "oldest_days_overdue": summary["oldest_days_overdue"],
                    },
                }

                outbox_row = EmailOutbox(
                    user_id=user.id,
                    customer_id=c["id"],
                    channel=channel,
                    template=template_key,
                    to_email=to_email,
                    subject=subj if channel == "email" else "SMS",
                    body=body,
                    payload_json=json.dumps(payload),
                    rule_id=rule.id if rule else None,
                    run_id=None,
                    status="queued",
                    next_attempt_at=now,
                )
                db.add(outbox_row)
                db.flush()
                enqueued += 1

                try:
                    inv_id = _oldest_overdue_invoice_id(db, user.id, c["id"])
                    if inv_id:
                        evt = ReminderEvent(
                            invoice_id=inv_id,
                            channel=channel,
                            template=template_key,
                            sent_at=now,
                            meta=json.dumps({
                                "customer_id": c["id"],
                                "days_overdue": days,
                                "sequence_id": seq_id,
                                "step_id": trigger.id,
                                "channel": channel,
                            }),
                        )
                        db.add(evt)
                        db.flush()
                except Exception as e_evt:
                    print("ERROR ReminderEvent for customer", c["id"], ":", repr(e_evt))

            sp.commit()
            jobs += enqueued

        except Exception as e_main:
            print("ERROR SendNow enqueue for customer", c["id"], ":", repr(e_main))
            sp.rollback()
            continue

    db.commit()

    return SendNowOut(
        ok=True,
        jobs=jobs,
        targeted_customers=len(pool),
    )

# ---------- Preview upcoming run times ----------

class PreviewOut(BaseModel):
    rule_id: int
    next_runs: List[str]

@router.get("/{rule_id}/preview", response_model=PreviewOut)
def preview(rule_id: int, days: int = 14, db: Session = Depends(get_db), user=Depends(require_user)):
    r = (
        db.query(ReminderRule)
          .filter(
              ReminderRule.id == rule_id,
              ReminderRule.user_id == user.id,
              ReminderRule.reminder_type == "chasing",
          )
          .first()
    )
    if not r:
        raise HTTPException(404, "Rule not found")

    tz = _user_tz(db, user.id)
    cur = r.reminder_next_run_utc or _next_local_daily_utc(_norm_time(r.reminder_time), tz)

    out = []
    end = datetime.utcnow() + timedelta(days=max(1, min(days, 90)))
    while cur <= end and len(out) < 30:
        out.append(cur.isoformat())
        cur = cur + timedelta(days=1)

    return PreviewOut(rule_id=r.id, next_runs=out)

# ---------- Scheduler-style enqueue for due runs ----------

@router.post("/enqueue-due")
def enqueue_due(db: Session = Depends(get_db)):
    try:
        now = datetime.utcnow()

        rules = (
            db.query(ReminderRule)
              .filter(
                  ReminderRule.reminder_type == "chasing",
                  ReminderRule.reminder_enabled == True,     # noqa: E712
                  ReminderRule.reminder_next_run_utc.isnot(None),
                  ReminderRule.reminder_next_run_utc <= now
              )
              .order_by(ReminderRule.reminder_next_run_utc.asc())
              .all()
        )

        processed = []

        for r in rules:
            user_id = r.user_id
            customers = _eligible_customers(db, user_id)
            sms_enabled, default_mode = _get_sms_settings(db, user_id)
            channels = _allowed_channels(default_mode, sms_enabled)

            jobs = 0
            for c in customers:
                try:
                    # sequence: customer override -> rule default
                    seq_id = c["seq_id"] or r.reminder_sequence_id
                    if not seq_id:
                        continue

                    days = _oldest_days_overdue(db, user_id, c["id"])
                    if days <= 0:
                        continue

                    for channel in channels:
                        if channel == "email":
                            to_email = (c.get("email") or "").strip()
                            if not to_email:
                                continue
                            if len(to_email) > 254:
                                to_email = to_email[:254]
                        else:
                            to_email = (c.get("phone") or "").strip()
                            if not to_email:
                                continue

                        trigger = _choose_step(db, seq_id, days, channel=channel)
                        if not trigger:
                            continue

                        tpl = _load_template(db, user_id, trigger.template_key, trigger.channel)
                        if not tpl:
                            continue

                        if _sent_recently(db, user_id, c["id"], trigger.template_key, channel):
                            continue

                        subj_raw = (tpl.subject or "").strip()
                        if len(subj_raw) > 255:
                            subj_raw = subj_raw[:255]

                        template_key = (trigger.template_key or "").strip()
                        if len(template_key) > 64:
                            template_key = template_key[:64]

                        summary = _customer_overdue_summary(db, user_id, c["id"])
                        ctx = {
                            "customer_name": summary["customer_name"],
                            "invoice_count": summary["invoice_count"],
                            "overdue_total": summary["overdue_total"],
                            "oldest_days_overdue": summary["oldest_days_overdue"],
                            "oldest_invoice": summary["oldest_invoice"],
                            "pay_url": summary["pay_url"],
                            "invoices_table": _invoices_table_html(summary["invoices"]),
                        }

                        body_html_raw = (tpl.body_html or "").strip()
                        body_text_raw = (tpl.body_text or "").strip()

                        subj      = _render_tokens(subj_raw,      ctx)
                        body_html = _render_tokens(body_html_raw, ctx)
                        body_text = _render_tokens(body_text_raw, ctx)
                        body      = body_text if channel == "sms" else (body_html or body_text)
                        if not body:
                            continue

                        payload = {
                            "sequence_id": seq_id,
                            "step_id": trigger.id,
                            "days_overdue": days,
                            "channel": channel,
                            "summary": {
                                "invoice_count": summary["invoice_count"],
                                "overdue_total": summary["overdue_total"],
                                "oldest_days_overdue": summary["oldest_days_overdue"],
                            }
                        }

                        job = EmailOutbox(
                            user_id=user_id,
                            customer_id=c["id"],
                            channel=channel,
                            template=template_key,
                            to_email=to_email,
                            subject=subj if channel == "email" else "SMS",
                            body=body,
                            payload_json=json.dumps(payload),
                            rule_id=r.id,
                            run_id=None,
                            status="queued",
                            next_attempt_at=now,
                        )
                        db.add(job)
                        db.flush()

                        inv_id = _oldest_overdue_invoice_id(db, user_id, c["id"])
                        if inv_id:
                            db.add(ReminderEvent(
                                invoice_id=inv_id,
                                channel=channel,
                                template=template_key,
                                sent_at=now,
                                meta=json.dumps({
                                    "customer_id": c["id"],
                                    "days_overdue": days,
                                    "sequence_id": seq_id,
                                    "step_id": trigger.id,
                                    "channel": channel,
                                })
                            ))
                            db.flush()

                        jobs += 1

                except Exception:
                    db.rollback()
                    continue

            # advance next run for this rule
            tz = _user_tz(db, user_id)
            r.reminder_last_run_utc = now
            try:
                run_time = _norm_time(r.reminder_time) if r.reminder_time else _norm_time("09:00")
                r.reminder_next_run_utc = _next_local_daily_utc(run_time, tz)
            except Exception:
                r.reminder_next_run_utc = now + timedelta(days=1)

            processed.append({"rule_id": r.id, "jobs": jobs})

        db.commit()
        return {"ok": True, "runs": processed}

    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"error": "".join(traceback.format_exception_only(type(e), e)).strip()}
        )

# ======== Global chasing config (hour, enabled, default plan, exclusions) ========

class ChasingGlobalsOut(BaseModel):
    enabled: bool
    hour: int                           # 0..23
    default_sequence_id: Optional[int] = None
    delivery_mode: str = "email"

class ChasingGlobalsIn(BaseModel):
    enabled: bool
    hour: int                           # 0..23
    default_sequence_id: Optional[int] = None
    delivery_mode: Optional[str] = None

def _ensure_chasing_global_rule(db: Session, user_id: int) -> None:
    """
    Make sure a global chasing ReminderRule exists (is_global=0, reminder_type='chasing').
    """
    row = db.execute(sqltext("""
        SELECT id FROM reminder_rules
         WHERE user_id=:uid AND reminder_type='chasing' AND is_global=0
         LIMIT 1
    """), {"uid": user_id}).first()
    if row:
        return

    tz = _user_tz(db, user_id)
    hhmm = "09:00"
    next_utc = _next_local_daily_utc(hhmm, tz)

    db.execute(sqltext("""
        INSERT INTO reminder_rules
            (user_id, name, reminder_type, reminder_sequence_id,
             reminder_frequency, reminder_time, reminder_enabled,
             is_global, reminder_next_run_utc, schedule, escalate, created_at)
        VALUES
            (:uid, :name, 'chasing', NULL,
             'daily', :t, 0,
             0, :nx, '', 0, NOW())
    """), {"uid": user_id, "name": "Chasing (global)", "t": hhmm, "nx": next_utc})
    db.commit()

def _get_chasing_global_rule(db: Session, user_id: int) -> Optional[dict]:
    row = db.execute(sqltext("""
        SELECT id, reminder_time, reminder_enabled, reminder_sequence_id
          FROM reminder_rules
         WHERE user_id=:uid AND reminder_type='chasing' AND is_global=0
         LIMIT 1
    """), {"uid": user_id}).mappings().first()
    return dict(row) if row else None

def _update_chasing_global_rule(
    db: Session,
    user_id: int,
    *,
    enabled: Optional[bool] = None,
    hour: Optional[int] = None,                   # 0..23
    default_sequence_id: Optional[int] = ...      # int | None | Ellipsis (no change)
) -> None:
    _ensure_chasing_global_rule(db, user_id)

    sets: list[str] = []
    params: Dict[str, object] = {"uid": user_id}

    if enabled is not None:
        sets.append("reminder_enabled = :en")
        params["en"] = 1 if enabled else 0

    if hour is not None:
        hh = max(0, min(23, int(hour)))
        params["t"] = f"{hh:02d}:00"
        sets.append("reminder_time = :t")

        tz = _user_tz(db, user_id)
        params["nx"] = _next_local_daily_utc(params["t"], tz)
        sets.append("reminder_next_run_utc = :nx")

    if default_sequence_id is not ...:
        if default_sequence_id is None:
            sets.append("reminder_sequence_id = NULL")
        else:
            plan = (
                db.query(ChasingPlan)
                  .filter(
                      ChasingPlan.id == int(default_sequence_id),
                      ChasingPlan.user_id == user_id,
                  )
                  .first()
            )
            if not plan:
                raise HTTPException(400, "Chasing plan does not exist")
            params["sid"] = int(default_sequence_id)
            sets.append("reminder_sequence_id = :sid")

    if not sets:
        return

    sql = f"""
        UPDATE reminder_rules
           SET {", ".join(sets)}
         WHERE user_id = :uid
           AND reminder_type = 'chasing'
           AND is_global = 0
         LIMIT 1
    """
    db.execute(sqltext(sql), params)
    db.commit()

@router.get("/globals", response_model=ChasingGlobalsOut)
def get_chasing_globals(db: Session = Depends(get_db), user=Depends(require_user)):
    _ensure_chasing_global_rule(db, user.id)
    row = _get_chasing_global_rule(db, user.id)
    if not row:
        raise HTTPException(500, "Global chasing rule missing")
    try:
        hh = int(_norm_time(row["reminder_time"]).split(":")[0])
    except Exception:
        hh = 9
    sms_settings = _ensure_sms_settings(db, user.id)
    delivery_mode = (sms_settings.chasing_delivery_mode or "email").lower()
    if delivery_mode not in ("email", "sms", "both"):
        delivery_mode = "email"
    return ChasingGlobalsOut(
        enabled = bool(row["reminder_enabled"]),
        hour = hh,
        default_sequence_id = row.get("reminder_sequence_id"),
        delivery_mode = delivery_mode,
    )

@router.post("/globals")
def save_chasing_globals(
    body: ChasingGlobalsIn,
    db: Session = Depends(get_db),
    user=Depends(require_user)
):
    _update_chasing_global_rule(
        db, user.id,
        enabled=bool(body.enabled),
        hour=int(body.hour),
        default_sequence_id=body.default_sequence_id if body.default_sequence_id is not None else None,
    )
    if body.delivery_mode is not None:
        mode = body.delivery_mode.lower().strip()
        if mode not in ("email", "sms", "both"):
            raise HTTPException(400, "delivery_mode must be email, sms, or both")
        sms_settings = _ensure_sms_settings(db, user.id)
        sms_settings.chasing_delivery_mode = mode
        db.add(sms_settings)
        db.commit()
    return {"ok": True}

# ----- Chasing global exclusions (frequency='chasing') -----

class ChasingExclusionIn(BaseModel):
    customer_id: int

@router.get("/exclusions")
def list_chasing_exclusions(db: Session = Depends(get_db), user=Depends(require_user)):
    rows = db.execute(sqltext("""
        SELECT e.customer_id, c.name AS customer_name, e.created_at
          FROM reminder_global_exclusions e
          LEFT JOIN customers c ON c.id = e.customer_id
         WHERE e.user_id=:uid AND e.frequency='chasing'
         ORDER BY c.name, e.customer_id
    """), {"uid": user.id}).mappings().all()
    return [
        {"customer_id": r["customer_id"], "customer_name": r.get("customer_name")}
        for r in rows
    ]

@router.post("/exclusions")
def add_chasing_exclusion(body: ChasingExclusionIn, db: Session = Depends(get_db), user=Depends(require_user)):
    db.execute(sqltext("""
        INSERT INTO reminder_global_exclusions (user_id, frequency, customer_id)
        VALUES (:uid, 'chasing', :cid)
        ON DUPLICATE KEY UPDATE created_at = created_at
    """), {"uid": user.id, "cid": int(body.customer_id)})
    db.commit()
    return {"ok": True}

@router.delete("/exclusions/{customer_id}")
def remove_chasing_exclusion(customer_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    db.execute(sqltext("""
        DELETE FROM reminder_global_exclusions
         WHERE user_id=:uid AND frequency='chasing' AND customer_id=:cid
    """), {"uid": user.id, "cid": int(customer_id)})
    db.commit()
    return {"ok": True}
