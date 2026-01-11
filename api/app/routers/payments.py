# api/app/routers/payments.py
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func
from ..shared import APIRouter, Depends, HTTPException, BaseModel, Session
from ..database import get_db
from ..models import Payment, PaymentAllocation, Invoice, Customer
from .invoices import _recalc_invoice_paid_fields  # reuse helper
from .auth import require_user                      # <-- enforce auth

router = APIRouter(prefix="/api/payments", tags=["payments"])

class AllocationIn(BaseModel):
    invoice_id: int
    amount: Decimal

class PaymentIn(BaseModel):
    customer_id: int
    amount: Decimal
    method: str
    received_at: Optional[str] = None
    note: Optional[str] = None
    source: str = "manual"
    allocations: List[AllocationIn] = []

@router.post("/record")
def record_payment(
    p: PaymentIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),                 # <-- require login
):
    # 1) Make sure this customer belongs to the logged-in user
    cust = (
        db.query(Customer)
          .filter(Customer.id == p.customer_id, Customer.user_id == user.id)
          .first()
    )
    if not cust:
        raise HTTPException(404, "Unknown customer")

    # 2) Basic checks
    if Decimal(p.amount) <= 0:
        raise HTTPException(400, "Payment amount must be positive")

    total_alloc = sum(Decimal(a.amount) for a in p.allocations) if p.allocations else Decimal("0")
    if total_alloc > Decimal(p.amount):
        raise HTTPException(400, "Allocations exceed payment amount")

    # 3) Create payment row (customer-scoped; no single-invoice FK)
    rec_at = datetime.fromisoformat(p.received_at) if p.received_at else datetime.utcnow()
    pay = Payment(
        customer_id=p.customer_id,
        amount=Decimal(p.amount),
        method=p.method,
        received_at=rec_at,
        source=p.source,
        note=(p.note or None),
    )
    db.add(pay); db.flush()  # need pay.id

    # 4) Optional allocations (each invoice must belong to the same user + customer)
    touched_invoices: list[Invoice] = []
    for a in p.allocations:
        inv = (
            db.query(Invoice)
              .filter(
                  Invoice.id == a.invoice_id,
                  Invoice.user_id == user.id,            # user owns it
                  Invoice.customer_id == p.customer_id,  # and same customer
              )
              .first()
        )
        if not inv:
            raise HTTPException(400, f"Invoice {a.invoice_id} not found for this customer")

        db.add(PaymentAllocation(
            payment_id=pay.id,
            invoice_id=inv.id,
            amount=Decimal(a.amount),
        ))
        touched_invoices.append(inv)

    # 5) Recalc any invoices we touched (keeps legacy fields in sync)
    for inv in touched_invoices:
        _recalc_invoice_paid_fields(db, inv)

    db.commit()
    remaining = Decimal(p.amount) - total_alloc
    return {
        "payment_id": pay.id,
        "allocated": float(total_alloc),
        "unallocated": float(remaining),
    }
