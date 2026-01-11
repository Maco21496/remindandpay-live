# api/app/routers/debug_list.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc
from ..database import get_db
from ..models import Invoice
from .auth import require_user

router = APIRouter(prefix="/api/debug", tags=["debug"])

@router.get("/recent-invoices")
def recent_invoices(limit: int = 50, db: Session = Depends(get_db), user = Depends(require_user)):
    rows = (
        db.query(Invoice)
          .filter(Invoice.user_id == user.id)
          .order_by(desc(Invoice.id))
          .limit(max(1, min(limit, 200)))
          .all()
    )
    return [
        {
            "id": r.id,
            "customer_id": r.customer_id,
            "invoice_number": r.invoice_number,
            "amount_due": float(r.amount_due or 0),
            "status": r.status,
            "due_date": r.due_date.isoformat() if r.due_date else None,
        }
        for r in rows
    ]
