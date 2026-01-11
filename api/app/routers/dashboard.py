# api/app/routers/dashboard.py
from datetime import datetime, timedelta, date, time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_
import traceback

from ..database import get_db
from ..models import Invoice, Customer, Payment, PaymentAllocation
from .auth import require_user

from math import ceil
from typing import Optional, List, Dict
from pydantic import BaseModel

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _open_cond():
    """Open = not paid and not written off."""
    return and_(Invoice.status != "paid", Invoice.status != "written_off")


def _remaining_amount_expr(db: Session, inv_alias=None):
    """
    SQL expression: amount_due - SUM(allocations.amount) for the current invoice.
    Correlated subquery so it can be used inside aggregates.
    """
    Inv = inv_alias or Invoice
    alloc_sum = (
        db.query(func.coalesce(func.sum(PaymentAllocation.amount), 0.0))
          .filter(PaymentAllocation.invoice_id == Inv.id)
          .correlate(Inv)
          .scalar_subquery()
    )
    return (Inv.amount_due - alloc_sum)

# --- Class ---

class TxRowOut(BaseModel):
    dt: str                 # ISO YYYY-MM-DD
    kind: str               # 'invoice' | 'payment'
    subkind: Optional[str]  # 'alloc' | 'unalloc' | None
    ref: str
    desc: str
    debit: float            # +ve adds to balance
    credit: float           # +ve reduces balance
    invoice_id: Optional[int] = None   # 

class TxPageOut(BaseModel):
    items: List[TxRowOut]
    page: int
    per_page: int
    total: int
    pages: int
    opening_balance: float
    paid_map: Optional[dict[int, float]] = None   

class WeekPoint(BaseModel):
    start: str            # ISO week start (Monday)
    total: float          # sum for the week
    invoice_count: int    # number of invoices issued that week (issued mode)

class WeeklyOut(BaseModel):
    from_date: str
    to_date: str
    points: List[WeekPoint]
    sum_total: float
    avg_invoice_amount: Optional[float] = None    # issued mode only
    avg_invoices_per_week: Optional[float] = None # issued mode only


@router.get("/summary")
def summary(
    customer_id: int | None = None,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    now = datetime.utcnow()
    start_month = datetime(now.year, now.month, 1)
    # next_month for an exclusive upper bound
    next_month = (start_month.replace(year=start_month.year + 1, month=1)
                  if start_month.month == 12 else
                  start_month.replace(month=start_month.month + 1))
    end_week = now + timedelta(days=(7 - now.weekday()))

    # Base filter: only this user's invoices
    filters = [Invoice.user_id == user.id]

    # Optional: scope to a single customer you own
    if customer_id:
        owned = (
            db.query(Customer.id)
              .filter(Customer.id == customer_id, Customer.user_id == user.id)
              .first()
        )
        if not owned:
            raise HTTPException(status_code=404, detail="Customer not found")
        filters.append(Invoice.customer_id == customer_id)

    open_cond = _open_cond()
    remaining = _remaining_amount_expr(db)
    open_remaining = case((open_cond, remaining), else_=0.0)

    # Totals (only open invoices count toward outstanding)
    outstanding_total = (
        db.query(func.coalesce(func.sum(open_remaining), 0.0))
          .filter(*filters)
          .scalar()
    )

    amount_overdue = (
        db.query(func.coalesce(func.sum(open_remaining), 0.0))
          .filter(open_cond, Invoice.due_date < now, *filters)
          .scalar()
    )

    amount_due_soon = (
        db.query(func.coalesce(func.sum(open_remaining), 0.0))
          .filter(open_cond, Invoice.due_date >= now, Invoice.due_date <= end_week, *filters)
          .scalar()
    )

    # Paid this month = sum of allocations whose payment.received_at is in this month,
    # but only for invoices belonging to this user.
    paid_this_month = (
        db.query(func.coalesce(func.sum(PaymentAllocation.amount), 0.0))
          .join(Payment, Payment.id == PaymentAllocation.payment_id)
          .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
          .filter(
              Payment.received_at >= start_month,
              Payment.received_at < next_month,
              Invoice.user_id == user.id,
          )
          .filter(*( [Invoice.customer_id == customer_id] if customer_id else [] ))
          .scalar()
    )

    # Aging buckets (remaining on open invoices)
    days_over = func.datediff(func.date(now), func.date(Invoice.due_date))

    def aging_sum(min_d, max_d=None):
        cond = and_(open_cond, Invoice.due_date < now, days_over >= min_d)
        if max_d is not None:
            cond = and_(cond, days_over <= max_d)
        return (
            db.query(func.coalesce(func.sum(case((cond, remaining), else_=0.0)), 0.0))
              .filter(*filters)
              .scalar()
        )

    aging = {
        "0_30":  float(aging_sum(1, 30) or 0.0),
        "31_60": float(aging_sum(31, 60) or 0.0),
        "61_90": float(aging_sum(61, 90) or 0.0),
        "90p":   float(aging_sum(91, None) or 0.0),
    }

    customers_count = (
        1 if customer_id else
        int(db.query(func.count(Customer.id))
              .filter(Customer.user_id == user.id)
              .scalar() or 0)
    )

    return {
        "outstanding_total": float(outstanding_total or 0.0),
        "overdue":           float(amount_overdue or 0.0),
        "due_soon":          float(amount_due_soon or 0.0),
        "paid_this_month":   float(paid_this_month or 0.0),
        "aging": aging,
        "counts": {
            "customers":      customers_count,
            "open_invoices":  int(db.query(func.count(Invoice.id)).filter(open_cond, *filters).scalar() or 0),
            "overdue":        int(db.query(func.count(Invoice.id)).filter(open_cond, Invoice.due_date < now, *filters).scalar() or 0),
            "due_soon":       int(db.query(func.count(Invoice.id)).filter(open_cond, Invoice.due_date >= now, Invoice.due_date <= end_week, *filters).scalar() or 0),
        },
    }


@router.get("/customers-aging")
def customers_aging(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    One row per customer, with:
      - total  : all OPEN remaining (invoices - allocations)
      - due_now: sum of OVERDUE remaining (due_date < today)
      - b0_30/31_60/61_90/90p: OVERDUE buckets only
    """
    open_cond = _open_cond()

    rows = (
        db.query(
            Invoice.id,
            Invoice.customer_id,
            Customer.name,
            Invoice.due_date,
            Invoice.amount_due,
        )
        .join(Customer, Customer.id == Invoice.customer_id)
        .filter(
            open_cond,
            Invoice.user_id == user.id,
            Customer.user_id == user.id,
        )
        .all()
    )

    today = date.today()
    out: dict[int, dict] = {}

    for inv_id, cid, cname, due_dt, amt_due in rows:
        # remaining = amount_due - allocations
        alloc = (
            db.query(func.coalesce(func.sum(PaymentAllocation.amount), 0.0))
              .filter(PaymentAllocation.invoice_id == inv_id)
              .scalar() or 0.0
        )
        remaining_val = float(amt_due or 0.0) - float(alloc or 0.0)
        if remaining_val <= 0:
            continue

        rec = out.setdefault(cid, {
            "customer_id":   cid,
            "customer_name": cname or f"Customer #{cid}",
            "total":   0.0,
            "due_now": 0.0,   # NEW: total currently overdue
            # Overdue-only buckets:
            "b0_30":  0.0,
            "b31_60": 0.0,
            "b61_90": 0.0,
            "b90p":   0.0,
        })
        rec["total"] += remaining_val  # always added to total

        # Only bucket if OVERDUE
        if due_dt:
            days_over = (today - due_dt.date()).days
            if days_over > 0:
                if   days_over <= 30:  bucket = "b0_30"
                elif days_over <= 60:  bucket = "b31_60"
                elif days_over <= 90:  bucket = "b61_90"
                else:                  bucket = "b90p"
                rec[bucket]  += remaining_val
                rec["due_now"] += remaining_val

    # Sort by total owed (desc)
    return sorted(out.values(), key=lambda r: r["total"], reverse=True)


@router.get("/customer-invoices")
def customer_invoices(
    customer_id: int,
    status: str = "open",  # "open" | "overdue" | "paid"
    limit: int = 100,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Return invoices for a single customer (default: open), oldest due first,
    with remaining computed from allocations, for the logged-in user only.
    """

    # Ownership check
    owned = (
        db.query(Customer.id)
          .filter(Customer.id == customer_id, Customer.user_id == user.id)
          .first()
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Customer not found")

    q = db.query(Invoice).filter(
        Invoice.customer_id == customer_id,
        Invoice.user_id == user.id,
    )

    if status == "open":
        q = q.filter(_open_cond())
    elif status == "overdue":
        q = q.filter(_open_cond(), Invoice.due_date < datetime.utcnow())
    elif status == "paid":
        q = q.filter(Invoice.status == "paid")

    q = q.order_by(Invoice.due_date.asc(), Invoice.id.asc()).limit(limit)
    rows = q.all()

    today = date.today()
    out = []
    for inv in rows:
        alloc = (
            db.query(func.coalesce(func.sum(PaymentAllocation.amount), 0.0))
              .filter(PaymentAllocation.invoice_id == inv.id)
              .scalar() or 0.0
        )
        remaining_val = float(inv.amount_due or 0.0) - float(alloc or 0.0)
        due = inv.due_date.date() if (inv.due_date and hasattr(inv.due_date, "date")) else None
        days_over = (today - due).days if due else None

        out.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "amount_due": remaining_val if status in ("open", "overdue") else float(inv.amount_due or 0.0),
            "status": inv.status,
            "days_overdue": days_over if (days_over and days_over > 0) else 0,
        })
    return out



def _ledger_rows_for_customer(
    db: Session,
    customer_id: int,
    user,
    date_from: Optional[str],
    date_to: Optional[str],
) -> List[TxRowOut]:
    """Build unified ledger rows (invoices + allocations + unallocated) for ONE customer."""
    # Ownership check
    owned = (
        db.query(Customer.id)
          .filter(Customer.id == customer_id, Customer.user_id == user.id)
          .first()
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Customer not found")

    df = datetime.fromisoformat(date_from).date() if date_from else None
    dt_ = datetime.fromisoformat(date_to).date() if date_to else None

    def within(col):
        conds = []
        if df:  conds.append(col >= df)
        if dt_: conds.append(col <= dt_)
        return and_(*conds) if conds else True

    rows: list[TxRowOut] = []

    # 1) Invoices (debits)
    inv_q = (
        db.query(Invoice.id, Invoice.invoice_number, Invoice.issue_date, Invoice.amount_due)
          .filter(Invoice.customer_id == customer_id, Invoice.user_id == user.id)
          .filter(Invoice.kind == "invoice")
          .filter(within(func.date(Invoice.issue_date)))
          .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
    )
    for i in inv_q.all():
        rows.append(TxRowOut(
            dt=i.issue_date.date().isoformat(),
            kind="invoice",
            subkind=None,
            ref=f"INV {i.invoice_number}",
            desc=f"Invoice {i.invoice_number}",
            debit=float(i.amount_due or 0.0),
            credit=0.0,
            invoice_id=i.id,  
        ))

    # 2) Allocated payments (credits)
    alloc_q = (
        db.query(
            PaymentAllocation.payment_id,
            PaymentAllocation.invoice_id,
            PaymentAllocation.amount,
            Payment.received_at,
            Invoice.invoice_number,
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .filter(Invoice.customer_id == customer_id, Invoice.user_id == user.id)
        .filter(within(func.date(Payment.received_at)))
        .order_by(Payment.received_at.asc(), PaymentAllocation.id.asc())
    )
    for a in alloc_q.all():
        rows.append(TxRowOut(
            dt=a.received_at.date().isoformat(),
            kind="payment",
            subkind="alloc",
            ref=f"PAY {a.payment_id}",
            desc=f"Payment → Inv {a.invoice_number}",
            debit=0.0,
            credit=float(a.amount or 0.0),
        ))

    # 3) Unallocated remainder for this customer's payments (still credits)
    sub = (
        db.query(
            PaymentAllocation.payment_id,
            func.coalesce(func.sum(PaymentAllocation.amount), 0.0).label("alloc")
        )
        .group_by(PaymentAllocation.payment_id)
        .subquery()
    )
    pay_q = (
        db.query(
            Payment.id,
            Payment.received_at,
            Payment.amount,
            (Payment.amount - func.coalesce(sub.c.alloc, 0.0)).label("unalloc"),
        )
        .outerjoin(sub, sub.c.payment_id == Payment.id)
        .filter(Payment.customer_id == customer_id)
        .filter(within(func.date(Payment.received_at)))
        .order_by(Payment.received_at.asc(), Payment.id.asc())
    )
    for p in pay_q.all():
        un = float(p.unalloc or 0.0)
        if un > 0:
            rows.append(TxRowOut(
                dt=p.received_at.date().isoformat(),
                kind="payment",
                subkind="unalloc",
                ref=f"PAY {p.id}",
                desc="Unallocated payment",
                debit=0.0,
                credit=un,
            ))

    return rows

@router.get("/customer-transactions", response_model=TxPageOut)
def customer_transactions(
    customer_id: int,
    page: int = 1,
    per_page: int = 20,                 # UI will offer 20 / 50 / 100
    date_from: Optional[str] = None,    # 'YYYY-MM-DD'
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    # Build full ledger (RowOut items)
    rows = _ledger_rows_for_customer(db, customer_id, user, date_from, date_to)

    # ---- Sort oldest -> newest for balance math ----
    rows_asc = sorted(rows, key=lambda r: ((r.dt or ""), r.kind, (r.ref or "")))

    # ---- Cumulative balance (oldest -> newest) ----
    bal = 0.0
    balances_asc: list[float] = []
    for r in rows_asc:
        bal += float(r.debit or 0.0) - float(r.credit or 0.0)
        balances_asc.append(round(bal, 2))

    # ---- Newest-first view for paging ----
    rows_desc = list(reversed(rows_asc))

    # Clamp per_page to allowed values (20/50/100)
    allowed = (20, 50, 100)
    per_page = per_page if per_page in allowed else min(allowed, key=lambda x: abs(x - per_page))
    page = max(1, page)

    total = len(rows_desc)
    pages = max(1, ceil(total / per_page))
    if page > pages:
        page = pages

    start = (page - 1) * per_page
    end = min(start + per_page, total)

    # Opening balance = balance before the OLDEST row on this page.
    # Oldest on this page in ASC index = total - end
    idx_before = (total - end) - 1
    opening_balance = balances_asc[idx_before] if idx_before >= 0 else 0.0

    # Slice page items (newest-first order)
    page_items = rows_desc[start:end]

    # --- NEW: paid-to-date across ALL history for the invoices on this page ---
    invoice_ids_on_page = [r.invoice_id for r in page_items if r.kind == "invoice" and r.invoice_id]
    paid_map: dict[int, float] = {}
    if invoice_ids_on_page:
        agg = (
            db.query(
                PaymentAllocation.invoice_id.label("invoice_id"),
                func.coalesce(func.sum(PaymentAllocation.amount), 0.0).label("paid")
            )
            .filter(PaymentAllocation.invoice_id.in_(invoice_ids_on_page))
            .group_by(PaymentAllocation.invoice_id)
            .all()
        )
        paid_map = {row.invoice_id: float(row.paid or 0.0) for row in agg}

    return TxPageOut(
        items=page_items,
        page=page,
        per_page=per_page,
        total=total,
        pages=pages,
        opening_balance=float(opening_balance),
        paid_map=paid_map, 
    )

@router.get("/sales-weekly", response_model=WeeklyOut)
def sales_weekly(
    weeks: int = 26,
    metric: str = "issued",   # "issued" (invoices - credits) | "received" (payments - refunds)
    customer_id: int | None = None,  
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Return last N weeks bucketed by Monday start:
      - metric=issued   -> invoice.amount_due for kind='invoice' minus kind='credit_note', by invoice.issue_date
      - metric=received -> payments kind='payment' minus 'refund', by payments.received_at
    Scoped to the logged-in user.
    """
    weeks = max(1, min(104, int(weeks)))
    today = date.today()
    start_dt = today - timedelta(days=(weeks * 7) - 1)

    def week_start(d: date) -> date:
        # Monday as start-of-week
        return d - timedelta(days=(d.weekday()))

    buckets: Dict[date, Dict[str, float|int]] = {}

    if metric == "issued":
        # Pull minimally required columns, scoped by owner and date window
        rows = (
            db.query(Invoice.kind, Invoice.issue_date, Invoice.amount_due)
              .filter(Invoice.user_id == user.id)
              .filter(func.date(Invoice.issue_date) >= start_dt)
              .filter(*( [Invoice.customer_id == customer_id] if customer_id else [] ))
              .all()
        )
        for kind, dt, amt in rows:
            if not dt or amt is None: 
                continue
            ws = week_start(dt.date())
            rec = buckets.setdefault(ws, {"total": 0.0, "count": 0})
            val = float(amt or 0.0) * (1.0 if kind == "invoice" else -1.0)  # credit_note subtracts
            rec["total"] += val
            if kind == "invoice":
                rec["count"] += 1

    else:  # metric == "received"
        from datetime import time
        start_ts = datetime.combine(start_dt, time.min)

        rows = (
            db.query(Payment.kind, Payment.received_at, Payment.amount)
            .join(Customer, Customer.id == Payment.customer_id)     # <-- scope by customer owner
            .filter(Customer.user_id == user.id)
            .filter(Payment.received_at.isnot(None))
            .filter(Payment.received_at >= start_ts)                # avoid func.date(...) issues
            .filter(*( [Payment.customer_id == customer_id] if customer_id else [] ))
            .all()
        )

        for kind, dt, amt in rows:
            if amt is None or dt is None:
                continue

            # normalise date (datetime/date/str)
            if isinstance(dt, datetime):
                base_d = dt.date()
            elif isinstance(dt, date):
                base_d = dt
            else:
                try:
                    base_d = datetime.fromisoformat(str(dt)).date()
                except Exception:
                    continue

            ws = week_start(base_d)
            rec = buckets.setdefault(ws, {"total": 0.0, "count": 0})

            k = (str(kind) or "").lower()
            sign = 1.0 if k == "payment" else -1.0   # refunds subtract
            rec["total"] += float(amt) * sign        # Decimal-safe

    # Build continuous series (every week from start to this week)
    points: List[WeekPoint] = []
    cursor = week_start(start_dt)
    end_ws = week_start(today)
    sum_total = 0.0
    total_invoice_amount = 0.0
    total_invoice_count = 0

    while cursor <= end_ws:
        rec = buckets.get(cursor, {"total": 0.0, "count": 0})
        t = float(rec.get("total", 0.0))
        c = int(rec.get("count", 0))
        points.append(WeekPoint(start=cursor.isoformat(), total=round(t, 2), invoice_count=c))
        sum_total += t
        total_invoice_amount += t if metric == "issued" else 0.0
        total_invoice_count  += c if metric == "issued" else 0
        cursor += timedelta(days=7)

    out = WeeklyOut(
        from_date=week_start(start_dt).isoformat(),
        to_date=end_ws.isoformat(),
        points=points,
        sum_total=round(sum_total, 2),
    )
    if metric == "issued" and total_invoice_count > 0:
        out.avg_invoice_amount   = round(total_invoice_amount / max(total_invoice_count, 1), 2)
        out.avg_invoices_per_week = round(total_invoice_count / max(len(points), 1), 2)

    return out