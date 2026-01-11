# api/app/routers/statements.py
from datetime import datetime, date
from typing import List, Optional, Dict

from sqlalchemy import func, and_, case
from ..shared import APIRouter, Depends, HTTPException, BaseModel, Session
from ..database import get_db
from fastapi.responses import Response
from ..services.statement_pdf import render_statement_pdf_html, render_pdf_from_html
from ..services.statements_logic import compute_statement_summary
from ..models import Customer, Invoice, Payment, PaymentAllocation
from ..schemas.statements import StatementOut
from .auth import require_user   # <-- enforce auth + ownership

router = APIRouter(prefix="/api/statements", tags=["statements"]) 

# ----------------------------
# Ledger (back-compat) models
# ----------------------------
class RowOut(BaseModel):
    dt: str                       # ISO YYYY-MM-DD
    kind: str                     # 'invoice' | 'payment'
    subkind: Optional[str] = None # 'alloc' | 'unalloc' | None
    ref: str
    desc: str
    debit: float                  # +ve adds to balance
    credit: float                 # +ve reduces balance

# Summary (new) models moved to api/app/schemas/statements.py and imported above


# -------------------------------------------------------------
# (1) LEDGER ENDPOINT — back compatible with existing frontend
#     GET /api/statements/customer/{id}
# -------------------------------------------------------------
@router.get("/customer/{customer_id}", response_model=List[RowOut])
def customer_ledger(
    customer_id: int,
    date_from: Optional[str] = None,  # 'YYYY-MM-DD'
    date_to: Optional[str] = None,    # 'YYYY-MM-DD'
    db: Session = Depends(get_db),
    user = Depends(require_user),     # <-- require login
):
    # Ownership check
    cust = (
        db.query(Customer)
          .filter(Customer.id == customer_id, Customer.user_id == user.id)
          .first()
    )
    if not cust:
        raise HTTPException(404, "Customer not found")

    df: Optional[date] = datetime.fromisoformat(date_from).date() if date_from else None
    dt_: Optional[date] = datetime.fromisoformat(date_to).date() if date_to else None

    def within(col):
        conds = []
        if df:  conds.append(col >= df)
        if dt_: conds.append(col <= dt_)
        return and_(*conds) if conds else True

    rows: list[RowOut] = []

    # 1) Invoices (debits) — scoped to this customer (and thus this user)
    inv_q = (
        db.query(Invoice.id, Invoice.invoice_number, Invoice.issue_date, Invoice.amount_due)
        .filter(Invoice.customer_id == customer_id)
        .filter(Invoice.kind == "invoice")
        .filter(within(func.date(Invoice.issue_date)))
        .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
    )
    for i in inv_q.all():
        rows.append(RowOut(
            dt=i.issue_date.date().isoformat(),
            kind="invoice",
            subkind=None,
            ref=f"INV {i.invoice_number}",
            desc=f"Invoice {i.invoice_number}",
            debit=float(i.amount_due or 0),
            credit=0.0,
        ))

    # 2) Allocated payments (credits applied to invoices for this customer)
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
        .filter(Invoice.customer_id == customer_id)
        .filter(within(func.date(Payment.received_at)))
        .order_by(Payment.received_at.asc(), PaymentAllocation.id.asc())
    )
    for a in alloc_q.all():
        rows.append(RowOut(
            dt=a.received_at.date().isoformat(),
            kind="payment",
            subkind="alloc",
            ref=f"PAY {a.payment_id}",
            desc=f"Payment → Inv {a.invoice_number}",
            debit=0.0,
            credit=float(a.amount or 0),
        ))

    # 3) Unallocated payment leftovers for this customer (still credits)
    sub = (
        db.query(
            PaymentAllocation.payment_id,
            func.coalesce(func.sum(PaymentAllocation.amount), 0).label("alloc")
        )
        .group_by(PaymentAllocation.payment_id)
        .subquery()
    )
    pay_q = (
        db.query(
            Payment.id,
            Payment.received_at,
            Payment.amount,
            (Payment.amount - func.coalesce(sub.c.alloc, 0)).label("unalloc"),
        )
        .outerjoin(sub, sub.c.payment_id == Payment.id)
        .filter(Payment.customer_id == customer_id)
        .filter(within(func.date(Payment.received_at)))
        .order_by(Payment.received_at.asc(), Payment.id.asc())
    )
    for p in pay_q.all():
        un = float(p.unalloc or 0)
        if un > 0:
            rows.append(RowOut(
                dt=p.received_at.date().isoformat(),
                kind="payment",
                subkind="unalloc",
                ref=f"PAY {p.id}",
                desc="Unallocated payment",
                debit=0.0,
                credit=un,
            ))

    rows.sort(key=lambda r: (r.dt, r.kind, r.ref))
    return rows

# -------------------------------------------------------------------
# (2) SUMMARY ENDPOINT — richer open/buckets/unallocated view
#     GET /api/statements/customer/{id}/summary
# -------------------------------------------------------------------
@router.get("/customer/{customer_id}/summary", response_model=StatementOut)
def customer_statement_summary(
    customer_id: int,
    date_from: Optional[str] = None,         # kept for parity; not used for aging
    date_to: Optional[str] = None,           # 'YYYY-MM-DD' — as-of date (inclusive)
    include_after_payments: Optional[bool] = False,  # if True, allocations ignore date_to cut-off
    db: Session = Depends(get_db),
    user = Depends(require_user),            # <-- require login
):
    return compute_statement_summary(
        db=db,
        user_id=user.id,
        customer_id=customer_id,
        date_to=date_to,
        include_after_payments=include_after_payments,
    )


# -------------------------------------------------------------
# (3) PDF ENDPOINT – server-rendered statement as PDF
#     GET /api/statements/customer/{id}/pdf
# -------------------------------------------------------------
@router.get("/customer/{customer_id}/pdf")
def customer_statement_pdf(
    customer_id: int,
    date_to: Optional[str] = None,
    include_after_payments: Optional[bool] = False,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    html = render_statement_pdf_html(
        db=db,
        user_id=user.id,
        customer_id=customer_id,
        date_to=date_to,
        include_after_payments=bool(include_after_payments),
    )
    if not html:
        raise HTTPException(404, "Unable to render statement HTML")

    pdf = render_pdf_from_html(html)
    if not pdf:
        raise HTTPException(500, "Failed to render PDF")

    cust = db.query(Customer).filter(Customer.id == customer_id, Customer.user_id == user.id).first()
    cname = (cust.name if cust else f"Customer-{customer_id}")
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-", " ") else "_" for ch in cname).strip().replace(" ", "_")
    suffix = f"-{date_to}" if date_to else ""
    filename = f"Statement-{safe}{suffix}.pdf"

    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\""}
    return Response(content=pdf, media_type="application/pdf", headers=headers)
