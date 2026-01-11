# app/services/statements_logic.py
from datetime import datetime, date
from typing import Optional, Dict, List

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from ..schemas.statements import (
    StatementOut, TotalsOut, BucketsOut, OpenInvoiceOut,
)
from ..models import Customer, Invoice, Payment, PaymentAllocation
from fastapi import HTTPException


def compute_statement_summary(
    db: Session,
    user_id: int,
    customer_id: int,
    date_to: Optional[str] = None,
    include_after_payments: bool = False,
) -> StatementOut:
    cust = (
        db.query(Customer)
          .filter(Customer.id == customer_id, Customer.user_id == user_id)
          .first()
    )
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")

    as_of: date = date.today()
    if date_to:
        as_of = datetime.fromisoformat(date_to).date()

    def on_or_before(col_dt):
        return func.date(col_dt) <= as_of

    alloc_q = (
        db.query(
            PaymentAllocation.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(PaymentAllocation.amount), 0).label("alloc_sum"),
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .filter(Payment.customer_id == customer_id)
        .filter(Payment.kind.in_(["payment", "refund"]))
    )
    if not include_after_payments:
        alloc_q = alloc_q.filter(on_or_before(Payment.received_at))
    alloc_q = alloc_q.group_by(PaymentAllocation.invoice_id)

    alloc_map: Dict[int, float] = {
        row.invoice_id: float(row.alloc_sum or 0.0) for row in alloc_q.all()
    }

    inv_q = (
        db.query(Invoice)
        .filter(
            Invoice.customer_id == customer_id,
            Invoice.kind == "invoice",
            on_or_before(Invoice.issue_date),
        )
    )
    invoices = inv_q.all()

    buckets = {"ov_0_30": 0.0, "ov_31_60": 0.0, "ov_61_90": 0.0, "ov_90p": 0.0}
    total_outstanding_gross = 0.0
    overdue_total = 0.0
    open_items: List[OpenInvoiceOut] = []

    for inv in invoices:
        total = float(inv.amount_due or 0.0)
        paid_as_of = float(alloc_map.get(inv.id, 0.0))
        outstanding = max(0.0, total - paid_as_of)
        if outstanding <= 0.0001:
            continue

        issue_dt = inv.issue_date.date() if inv.issue_date else None
        due_dt = inv.due_date.date() if inv.due_date else (issue_dt or as_of)
        days_overdue = 0
        if due_dt and as_of > due_dt:
            days_overdue = (as_of - due_dt).days

        total_outstanding_gross += outstanding

        if days_overdue > 0:
            overdue_total += outstanding
            if days_overdue <= 30:
                buckets["ov_0_30"] += outstanding
            elif days_overdue <= 60:
                buckets["ov_31_60"] += outstanding
            elif days_overdue <= 90:
                buckets["ov_61_90"] += outstanding
            else:
                buckets["ov_90p"] += outstanding

        open_items.append(
            OpenInvoiceOut(
                id=inv.id,
                ref=getattr(inv, "invoice_number", str(inv.id)),
                desc=f"Invoice {getattr(inv, 'invoice_number', inv.id)}",
                issue_date=issue_dt.isoformat() if issue_dt else None,
                due_date=due_dt.isoformat() if due_dt else None,
                total=round(total, 2),
                paid_to_date=round(paid_as_of, 2),
                outstanding=round(outstanding, 2),
                days_overdue=days_overdue,
            )
        )

    payments_sum_q = (
        db.query(
            func.coalesce(
                func.sum(
                    case(
                        (Payment.kind == "payment", Payment.amount),
                        else_=-Payment.amount,
                    )
                ),
                0.0,
            )
        )
        .filter(Payment.customer_id == customer_id)
    )
    if not include_after_payments:
        payments_sum_q = payments_sum_q.filter(on_or_before(Payment.received_at))
    total_payments_net = float(payments_sum_q.scalar() or 0.0)

    alloc_total_q = (
        db.query(func.coalesce(func.sum(PaymentAllocation.amount), 0.0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .filter(Payment.customer_id == customer_id)
    )
    if not include_after_payments:
        alloc_total_q = alloc_total_q.filter(on_or_before(Payment.received_at))
    total_allocated = float(alloc_total_q.scalar() or 0.0)

    unallocated_credits = max(0.0, total_payments_net - total_allocated)
    balance_due = max(0.0, total_outstanding_gross - unallocated_credits)

    open_items.sort(key=lambda x: (x.issue_date or "", x.id))

    return StatementOut(
        as_of=as_of.isoformat(),
        totals=TotalsOut(
            total_outstanding_gross=round(total_outstanding_gross, 2),
            unallocated_credits=round(unallocated_credits, 2),
            balance_due=round(balance_due, 2),
            overdue_total=round(overdue_total, 2),
        ),
        buckets=BucketsOut(
            overdue_0_30=round(buckets["ov_0_30"], 2),
            overdue_31_60=round(buckets["ov_31_60"], 2),
            overdue_61_90=round(buckets["ov_61_90"], 2),
            overdue_90p=round(buckets["ov_90p"], 2),
        ),
        open_invoices=open_items,
    )
