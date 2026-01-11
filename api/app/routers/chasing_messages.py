# api/app/routers/chasing_messages.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ChasingPlan, ChasingTrigger, Customer
from .auth import require_user

router = APIRouter(prefix="/api/chasing_messages", tags=["chasing_messages"])

# These tiny "schema" classes are just placeholders so FastAPI can produce JSON.
class StepOut(dict):
    pass

class SequenceOut(dict):
    pass

# ----- Sequences / Plans -----

@router.get("/sequences")
def list_sequences(
    db: Session = Depends(get_db),
    user = Depends(require_user),
) -> List[SequenceOut]:

    # load all plans for this user
    plans = (
        db.query(ChasingPlan)
          .filter(ChasingPlan.user_id == user.id)
          .all()
    )

    out: List[SequenceOut] = []

    for plan in plans:
        # load that plan's triggers (formerly "steps")
        triggers = (
            db.query(ChasingTrigger)
              .filter(ChasingTrigger.sequence_id == plan.id)
              .order_by(ChasingTrigger.order_index.asc())
              .all()
        )

        out.append({
            "id": plan.id,
            "name": plan.name,
            "steps": [
                {
                    "id": trig.id,
                    "order_index": trig.order_index,
                    "offset_days": trig.offset_days,
                    "channel": trig.channel,
                    "template_key": trig.template_key,
                }
                for trig in triggers
            ],
        })

    return out


# ----- Overrides (per customer plan assignment) -----
# This part is still using the old column on Customer:
# Customer.reminder_sequence_id
# We have NOT renamed that to chasing_plan_id yet,
# so we keep using it here.

class OverrideIn(dict):
    pass

@router.get("/overrides")
def list_overrides(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    rows = (
        db.query(Customer)
          .filter(Customer.user_id == user.id)
          .order_by(Customer.name.asc())
          .all()
    )

    out = []
    for c in rows:
        out.append({
            "customer_id": c.id,
            "customer_name": c.name,
            # this is still the old FK column on customers.
            # it'll become chasing_plan_id in the next rename step.
            "sequence_id": c.reminder_sequence_id,
        })
    return out


@router.put("/overrides/{customer_id}")
def upsert_override(
    customer_id: int,
    body: OverrideIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    c = (
        db.query(Customer)
          .filter(Customer.id == customer_id, Customer.user_id == user.id)
          .first()
    )
    if not c:
        raise HTTPException(404, "Customer not found")

    # Accept {"sequence_id": <int|null>} or {"off": true}
    seq_id = body.get("sequence_id")
    turn_off = body.get("off") is True

    if turn_off:
        c.reminder_sequence_id = None
    else:
        if seq_id is not None:
            # make sure the plan actually exists and belongs to this user
            plan = (
                db.query(ChasingPlan)
                  .filter(ChasingPlan.id == seq_id, ChasingPlan.user_id == user.id)
                  .first()
            )
            if not plan:
                raise HTTPException(400, "Plan does not exist")
            c.reminder_sequence_id = int(seq_id)
        else:
            c.reminder_sequence_id = None

    db.commit()
    return {"ok": True}
