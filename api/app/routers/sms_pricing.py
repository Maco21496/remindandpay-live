# api/app/routers/sms_pricing.py
from typing import Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from ..models import SmsPricingSettings
from .auth import require_owner

router = APIRouter(prefix="/api/admin/sms_pricing", tags=["sms_pricing"])

DEFAULT_PRICING = {
    "sms_starting_credits": 1000,
    "sms_monthly_number_cost": 100,
    "sms_send_cost": 5,
    "sms_forward_cost": 5,
    "sms_suspend_after_days": 14,
}

class PricingOut(BaseModel):
    sms_starting_credits: int
    sms_monthly_number_cost: int
    sms_send_cost: int
    sms_forward_cost: int
    sms_suspend_after_days: int

class PricingIn(BaseModel):
    sms_starting_credits: Optional[int] = Field(None, ge=0)
    sms_monthly_number_cost: Optional[int] = Field(None, ge=0)
    sms_send_cost: Optional[int] = Field(None, ge=0)
    sms_forward_cost: Optional[int] = Field(None, ge=0)
    sms_suspend_after_days: Optional[int] = Field(None, ge=0)


def _ensure_pricing(db: Session) -> SmsPricingSettings:
    row = db.query(SmsPricingSettings).order_by(SmsPricingSettings.id.asc()).first()
    if row:
        return row
    row = SmsPricingSettings(**DEFAULT_PRICING)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

@router.get("", response_model=PricingOut)
def get_pricing(
    db: Session = Depends(get_db),
    owner=Depends(require_owner),
):
    row = _ensure_pricing(db)
    return PricingOut(
        sms_starting_credits=row.sms_starting_credits,
        sms_monthly_number_cost=row.sms_monthly_number_cost,
        sms_send_cost=row.sms_send_cost,
        sms_forward_cost=row.sms_forward_cost,
        sms_suspend_after_days=row.sms_suspend_after_days,
    )

@router.post("", response_model=PricingOut)
def update_pricing(
    payload: PricingIn,
    db: Session = Depends(get_db),
    owner=Depends(require_owner),
):
    row = _ensure_pricing(db)

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(row, field, value)

    db.add(row)
    db.commit()
    db.refresh(row)

    return PricingOut(
        sms_starting_credits=row.sms_starting_credits,
        sms_monthly_number_cost=row.sms_monthly_number_cost,
        sms_send_cost=row.sms_send_cost,
        sms_forward_cost=row.sms_forward_cost,
        sms_suspend_after_days=row.sms_suspend_after_days,
    )
