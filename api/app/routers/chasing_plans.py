# api/app/routers/chasing_plans.py
from __future__ import annotations
from typing import List, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from .auth import require_user
from ..models import ChasingPlan, ChasingTrigger, ReminderTemplate

router = APIRouter(prefix="/api/chasing_plans", tags=["chasing_plans"])

# ---------- Schemas ----------

class StepOut(BaseModel):
    """
    This still returns 'step' objects to the frontend so we don't
    break message_cycles.js. Under the hood it's coming from ChasingTrigger.
    """
    id: int
    sequence_id: int            # still called sequence_id in DB
    offset_days: int
    channel: str
    template_key: str
    order_index: int
    template_subject: Optional[str] = None
    template_tag: Optional[str] = None
    template_body_text: Optional[str] = None
    template_body_html: Optional[str] = None

    class Config:
        orm_mode = True

class SequenceOut(BaseModel):
    """
    This is effectively a ChasingPlan with its triggers.
    We keep the name 'SequenceOut' for now because the JS expects {id,name,steps:[...]}.
    """
    id: int
    name: str
    steps: List[StepOut] = []

    class Config:
        orm_mode = True

class SequenceListItem(BaseModel):
    """
    Summary row for the left-hand list.
    """
    id: int
    name: str
    step_count: int
    offsets: List[int] = []

class SequenceCreateIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)

class SequenceUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=64)

class StepCreateIn(BaseModel):
    offset_days: int = Field(..., ge=0)
    template_key: str
    channel: str = "email"

    @validator("channel")
    def _ch(cls, v):
        v = (v or "").lower()
        if v not in ("email", "sms"):
            raise ValueError("channel must be 'email' or 'sms'")
        return v

class StepUpdateIn(BaseModel):
    offset_days: Optional[int] = Field(None, ge=0)
    template_key: Optional[str] = None
    channel: Optional[str] = None  # rarely used; keep for parity

    @validator("channel")
    def _ch(cls, v):
        if v is None:
            return v
        v = v.lower()
        if v not in ("email", "sms"):
            raise ValueError("channel must be 'email' or 'sms'")
        return v

# ---------- Helpers ----------

def _ensure_owned(db: Session, user_id: int, plan_id: int) -> ChasingPlan:
    """
    Make sure the ChasingPlan exists and belongs to this user.
    (plan_id was seq_id in the old world)
    """
    plan = db.get(ChasingPlan, plan_id)
    if not plan or getattr(plan, "user_id", None) != user_id:
        raise HTTPException(404, "Plan not found")
    return plan

def _resequence(db: Session, plan_id: int) -> None:
    """
    Recompute order_index for all triggers in this plan based on:
    offset_days ASC, id ASC.

    Note: the column is still called sequence_id in the DB,
    so we filter on ChasingTrigger.sequence_id.
    """
    triggers = (
        db.query(ChasingTrigger)
          .filter(ChasingTrigger.sequence_id == plan_id)
          .order_by(ChasingTrigger.offset_days.asc(), ChasingTrigger.id.asc())
          .all()
    )
    for idx, trig in enumerate(triggers, start=1):
        trig.order_index = idx
        db.add(trig)
    db.commit()

def _step_joined(db: Session, user_id: int, trig: ChasingTrigger) -> StepOut:
    """
    For each trigger, also pull the linked template details to show
    subject/body/etc in the UI.
    """
    subj, tag, body_text, body_html = db.execute(
        select(
            ReminderTemplate.subject,
            ReminderTemplate.tag,
            ReminderTemplate.body_text,
            ReminderTemplate.body_html,
        ).where(
            ReminderTemplate.user_id == user_id,
            ReminderTemplate.key == trig.template_key,
            ReminderTemplate.channel == trig.channel,
        ).limit(1)
    ).one_or_none() or (None, None, None, None)

    return StepOut(
        id=trig.id,
        sequence_id=trig.sequence_id,
        offset_days=trig.offset_days,
        channel=trig.channel,
        template_key=trig.template_key,
        order_index=trig.order_index,
        template_subject=subj,
        template_tag=tag,
        template_body_text=body_text,
        template_body_html=body_html,
    )

# ---------- Routes ----------

@router.get("", response_model=List[SequenceListItem])
def list_sequences(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Left sidebar list: name, how many triggers, and which overdue days.
    """
    # get all the user's plans
    plans = (
        db.query(ChasingPlan.id, ChasingPlan.name)
          .filter(ChasingPlan.user_id == user.id)
          .order_by(ChasingPlan.name.asc())
          .all()
    )

    out: List[SequenceListItem] = []

    for plan_id, plan_name in plans:
        # get all offset_days for this plan's triggers
        offsets = (
            db.query(ChasingTrigger.offset_days)
              .filter(ChasingTrigger.sequence_id == plan_id)
              .order_by(
                  ChasingTrigger.offset_days.asc(),
                  ChasingTrigger.id.asc(),
              )
              .all()
        )
        offsets_only = [int(row.offset_days) for row in offsets]

        out.append(SequenceListItem(
            id=plan_id,
            name=plan_name,
            step_count=len(offsets_only),
            offsets=offsets_only,
        ))
    return out


@router.get("/{plan_id}", response_model=SequenceOut)
def get_sequence(
    plan_id: int,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Right-hand panel details for a single plan.
    """
    plan = _ensure_owned(db, user.id, plan_id)

    triggers = (
        db.query(ChasingTrigger)
          .filter(ChasingTrigger.sequence_id == plan.id)
          .order_by(
              ChasingTrigger.offset_days.asc(),
              ChasingTrigger.id.asc(),
          )
          .all()
    )

    return SequenceOut(
        id=plan.id,
        name=plan.name,
        steps=[_step_joined(db, user.id, trig) for trig in triggers],
    )


@router.post("", response_model=SequenceOut)
def create_sequence(
    body: SequenceCreateIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Create a new chasing plan for this user.
    """
    plan = ChasingPlan(user_id=user.id, name=body.name)
    db.add(plan)
    db.commit()
    db.refresh(plan)

    return SequenceOut(id=plan.id, name=plan.name, steps=[])


@router.patch("/{plan_id}", response_model=SequenceOut)
def update_sequence(
    plan_id: int,
    body: SequenceUpdateIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Rename a plan, etc.
    """
    plan = _ensure_owned(db, user.id, plan_id)
    if body.name is not None:
        plan.name = body.name
        db.add(plan)
        db.commit()
    return get_sequence(plan_id, db, user)


@router.delete("/{plan_id}")
def delete_sequence(
    plan_id: int,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Delete a chasing plan and all its triggers.
    """
    plan = _ensure_owned(db, user.id, plan_id)

    # delete triggers first
    db.query(ChasingTrigger).where(ChasingTrigger.sequence_id == plan.id).delete()

    db.delete(plan)
    db.commit()
    return {"ok": True}


@router.post("/{plan_id}/steps", response_model=StepOut)
def add_step(
    plan_id: int,
    body: StepCreateIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Add a new trigger (offset_days → send template_key) to this plan.
    """
    _ensure_owned(db, user.id, plan_id)

    # uniqueness check: only one trigger per (plan, channel, offset_days)
    exists = (
        db.query(ChasingTrigger.id)
          .filter_by(
              sequence_id=plan_id,
              channel=body.channel,
              offset_days=body.offset_days,
          )
          .first()
    )
    if exists:
        raise HTTPException(
            409,
            "A step already exists at that overdue day for this channel",
        )

    trig = ChasingTrigger(
        sequence_id=plan_id,
        offset_days=body.offset_days,
        channel=body.channel,
        template_key=body.template_key,
        order_index=9999,  # temporary; we'll resequence
    )
    db.add(trig)
    db.commit()
    db.refresh(trig)

    _resequence(db, plan_id)
    db.refresh(trig)

    return _step_joined(db, user.id, trig)


@router.patch("/{plan_id}/steps/{step_id}", response_model=StepOut)
def update_step(
    plan_id: int,
    step_id: int,
    body: StepUpdateIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Edit a trigger: change offset_day, change template, etc.
    """
    _ensure_owned(db, user.id, plan_id)

    trig = db.get(ChasingTrigger, step_id)
    if not trig or trig.sequence_id != plan_id:
        raise HTTPException(404, "Step not found")

    # Update channel if provided
    if body.channel is not None:
        trig.channel = body.channel

    # Update template_key if provided
    if body.template_key is not None:
        trig.template_key = body.template_key

    # Update offset_days if provided, but enforce uniqueness rule
    if body.offset_days is not None:
        clash = (
            db.query(ChasingTrigger.id)
              .filter(
                  ChasingTrigger.sequence_id == plan_id,
                  ChasingTrigger.channel == (body.channel or trig.channel),
                  ChasingTrigger.offset_days == body.offset_days,
                  ChasingTrigger.id != trig.id,
              )
              .first()
        )
        if clash:
            raise HTTPException(
                409,
                "A step already exists at that overdue day for this channel",
            )
        trig.offset_days = body.offset_days

    db.add(trig)
    db.commit()

    _resequence(db, plan_id)
    db.refresh(trig)

    return _step_joined(db, user.id, trig)


@router.delete("/{plan_id}/steps/{step_id}")
def remove_step(
    plan_id: int,
    step_id: int,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Delete a trigger from the plan.
    """
    _ensure_owned(db, user.id, plan_id)

    trig = db.get(ChasingTrigger, step_id)
    if not trig or trig.sequence_id != plan_id:
        raise HTTPException(404, "Step not found")

    db.delete(trig)
    db.commit()

    _resequence(db, plan_id)

    return {"ok": True}
