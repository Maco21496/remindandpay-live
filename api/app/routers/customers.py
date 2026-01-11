from datetime import datetime
from typing import Optional, List
from sqlalchemy import or_
from ..shared import APIRouter, Depends, HTTPException, Query, BaseModel, Field, Session
from ..database import get_db
from ..models import Customer, Invoice, User
from ..routers.auth import require_user
from ..calculate_due_date import compute_due_date

router = APIRouter(prefix="/api/customers", tags=["customers"])

# ---------- Schemas ----------

class CustomerIn(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None

    # address
    billing_line1: Optional[str] = None
    billing_line2: Optional[str] = None
    billing_city: Optional[str] = None
    billing_region: Optional[str] = None
    billing_postcode: Optional[str] = None
    billing_country: str = "GB"

    # terms
    terms_type: str = "net_30"
    terms_days: Optional[int] = None

class CustomerOut(BaseModel):
    id: int
    name: str
    email: Optional[str]
    phone: Optional[str]

    billing_line1: Optional[str] = None
    billing_line2: Optional[str] = None
    billing_city: Optional[str] = None
    billing_region: Optional[str] = None
    billing_postcode: Optional[str] = None
    billing_country: str = "GB"

    terms_type: Optional[str] = None
    terms_days: Optional[int] = None

    created_at: Optional[str] = None  # for list table

class CustomerUpdate(CustomerIn):
    recalc_due_dates: bool = False

# ---------- Routes ----------

@router.post("", response_model=CustomerOut)
def create_customer(
    payload: CustomerIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = Customer(
        user_id=user.id,
        name=payload.name.strip(),
        email=(payload.email or None),
        phone=(payload.phone or None),

        billing_line1=(payload.billing_line1 or None),
        billing_line2=(payload.billing_line2 or None),
        billing_city=(payload.billing_city or None),
        billing_region=(payload.billing_region or None),
        billing_postcode=(payload.billing_postcode or None),
        billing_country=(payload.billing_country or "GB").upper(),

        terms_type=payload.terms_type,
        terms_days=payload.terms_days,
    )
    db.add(row); db.commit(); db.refresh(row)
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "billing_line1": row.billing_line1,
        "billing_line2": row.billing_line2,
        "billing_city": row.billing_city,
        "billing_region": row.billing_region,
        "billing_postcode": row.billing_postcode,
        "billing_country": row.billing_country,
        "terms_type": row.terms_type,
        "terms_days": row.terms_days,
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
    }

@router.get("")
def list_customers(
    q: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    query = db.query(Customer).filter(Customer.user_id == user.id)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Customer.name.ilike(like),
                Customer.email.ilike(like),
                Customer.phone.ilike(like),
            )
        )
    items = query.order_by(Customer.created_at.desc()).limit(limit).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "phone": c.phone,
            "billing_line1": c.billing_line1,
            "billing_line2": c.billing_line2,
            "billing_city": c.billing_city,
            "billing_region": c.billing_region,
            "billing_postcode": c.billing_postcode,
            "billing_country": c.billing_country,
            "terms_type": c.terms_type,
            "terms_days": c.terms_days,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in items
    ]

@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    c = (
        db.query(Customer)
          .filter(Customer.id == customer_id, Customer.user_id == user.id)
          .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "phone": c.phone,
        "billing_line1": c.billing_line1,
        "billing_line2": c.billing_line2,
        "billing_city": c.billing_city,
        "billing_region": c.billing_region,
        "billing_postcode": c.billing_postcode,
        "billing_country": c.billing_country,
        "terms_type": c.terms_type,
        "terms_days": c.terms_days,
        "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
    }

@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    c = (
        db.query(Customer)
          .filter(Customer.id == customer_id, Customer.user_id == user.id)
          .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")

    # --- update customer fields ---
    c.name = payload.name.strip()
    c.email = payload.email or None
    c.phone = payload.phone or None

    c.billing_line1 = payload.billing_line1 or None
    c.billing_line2 = payload.billing_line2 or None
    c.billing_city = payload.billing_city or None
    c.billing_region = payload.billing_region or None
    c.billing_postcode = payload.billing_postcode or None
    c.billing_country = (payload.billing_country or "GB").upper()

    c.terms_type = payload.terms_type
    c.terms_days = payload.terms_days

    db.add(c)
    db.commit()
    db.refresh(c)

    # --- optional: recalc due dates for this user's open invoices for this customer ---
    if payload.recalc_due_dates:
        q = (
            db.query(Invoice)
              .filter(Invoice.customer_id == customer_id, Invoice.user_id == user.id)
              .filter(Invoice.status != "paid")
        )
        for inv in q:
            if not inv.issue_date:
                continue
            issue_dt = (
                inv.issue_date
                if isinstance(inv.issue_date, datetime)
                else datetime.combine(inv.issue_date, datetime.min.time())
            )
            new_due = compute_due_date(issue_dt, c.terms_type, c.terms_days)
            inv.due_date = new_due
            inv.terms_type = c.terms_type
            inv.terms_days = c.terms_days

        db.commit()

    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "phone": c.phone,
        "billing_line1": c.billing_line1,
        "billing_line2": c.billing_line2,
        "billing_city": c.billing_city,
        "billing_region": c.billing_region,
        "billing_postcode": c.billing_postcode,
        "billing_country": c.billing_country,
        "terms_type": c.terms_type,
        "terms_days": c.terms_days,
        "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
    }
