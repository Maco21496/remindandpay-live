# api/app/routers/outbox.py
from datetime import date
from math import ceil
from typing import Optional, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from ..database import get_db
from ..models import EmailOutbox, Customer
from .auth import require_user

router = APIRouter(prefix="/api/outbox", tags=["outbox"])

class OutboxRow(BaseModel):
    id: int
    to_email: str
    subject: str
    status: str                # queued|processing|sent|failed|canceled
    delivery_status: str       # queued|sent|delivered|bounced|complained|deferred
    attempt_count: int
    provider_message_id: Optional[str]
    created_at: str
    updated_at: str
    next_attempt_at: Optional[str] = None     # ⟵ added
    delivered_at: Optional[str] = None
    bounced_at: Optional[str] = None
    complained_at: Optional[str] = None
    rule_id: Optional[int] = None
    run_id: Optional[int] = None
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    last_error: Optional[str] = None
    delivery_detail: Optional[dict] = None

class PageOut(BaseModel):
    items: List[OutboxRow]
    page: int
    per_page: int
    total: int
    pages: int

@router.get("", response_model=PageOut)
def list_outbox(
    status: str = "all",
    search: Optional[str] = None,
    rule_id: Optional[int] = None,
    run_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    allowed = (20, 50, 100)
    if per_page not in allowed:
        per_page = min(allowed, key=lambda x: abs(x - per_page))
    page = max(1, page)

    o = EmailOutbox
    c = Customer

    q = (
        db.query(
            o.id,
            o.to_email,
            o.subject,
            o.status,
            o.delivery_status,
            o.attempt_count,
            o.provider_message_id,
            o.created_at,
            o.updated_at,
            o.next_attempt_at,                     # ⟵ added
            o.delivered_at,
            o.bounced_at,
            o.complained_at,
            o.rule_id,
            o.run_id,
            o.customer_id,
            c.name.label("customer_name"),
            o.last_error,
            o.delivery_detail,
        )
        .outerjoin(c, c.id == o.customer_id)
        .filter(o.user_id == user.id)
    )

    if rule_id is not None:
        q = q.filter(o.rule_id == rule_id)
    if run_id is not None:
        q = q.filter(o.run_id == run_id)
    if customer_id is not None:
        q = q.filter(o.customer_id == customer_id)

    if date_from:
        q = q.filter(func.date(o.created_at) >= date.fromisoformat(date_from))
    if date_to:
        q = q.filter(func.date(o.created_at) <= date.fromisoformat(date_to))

    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(o.to_email.ilike(like), o.subject.ilike(like)))

    # status mapping (UI convenience)
    if status in ("queued", "processing", "sent", "failed", "canceled"):
        q = q.filter(o.status == status)
    elif status in ("delivered", "bounced", "complained"):
        q = q.filter(o.delivery_status == status)

    q = q.order_by(o.created_at.desc(), o.id.desc())

    total = q.count()
    pages = max(1, ceil(total / per_page))
    if page > pages:
        page = pages

    rows = q.limit(per_page).offset((page - 1) * per_page).all()

    items: list[OutboxRow] = []
    for r in rows:
        items.append(OutboxRow(
            id=r.id,
            to_email=r.to_email,
            subject=r.subject or "",
            status=r.status,
            delivery_status=r.delivery_status,
            attempt_count=r.attempt_count or 0,
            provider_message_id=r.provider_message_id,
            created_at=r.created_at.isoformat() if r.created_at else None,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
            next_attempt_at=r.next_attempt_at.isoformat() if r.next_attempt_at else None,  # ⟵ added
            delivered_at=r.delivered_at.isoformat() if r.delivered_at else None,
            bounced_at=r.bounced_at.isoformat() if r.bounced_at else None,
            complained_at=r.complained_at.isoformat() if r.complained_at else None,
            rule_id=r.rule_id,
            run_id=r.run_id,
            customer_id=r.customer_id,
            customer_name=r.customer_name,
            last_error=r.last_error,
            delivery_detail=r.delivery_detail,
        ))

    return PageOut(items=items, page=page, per_page=per_page, total=total, pages=pages)
