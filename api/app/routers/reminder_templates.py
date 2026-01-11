# FINAL VERSION OF app/routers/reminder_templates.py
from __future__ import annotations

from typing import Optional, List, Literal, Any, Dict
from datetime import datetime

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator, root_validator
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, or_, and_, func, String, case

from ..shared import APIRouter
from ..database import get_db
from .auth import require_user

from ..models import ReminderTemplate  # SQLAlchemy model

# Optional: lightweight Jinja rendering for preview
try:
    from jinja2 import Template as JinjaTemplate
except Exception:  # pragma: no cover
    JinjaTemplate = None  # preview will gracefully error if jinja2 isn't installed

router = APIRouter(prefix="/api/reminder_templates", tags=["reminder_templates"])

# ----------------------------- Pydantic -----------------------------

Channel = Literal["email", "sms"]
Tag = Literal["gentle", "firm", "aggressive", "custom"]

class TemplateOut(BaseModel):
    id: int
    key: str
    channel: Channel
    tag: Tag
    step_number: Optional[int] = None
    name: str
    subject: Optional[str] = None
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    is_active: bool
    updated_at: datetime

    class Config:
        orm_mode = True


class TemplateCreateIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=64, description="Unique per user")
    channel: Channel
    tag: Tag = "custom"
    step_number: Optional[int] = Field(None, ge=0, le=99)
    name: str = Field(..., min_length=2, max_length=120)
    subject: Optional[str] = Field(None, max_length=255)
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    is_active: bool = True

    @validator("subject")
    def subject_required_for_email(cls, v, values):
        if values.get("channel") == "email" and not v:
            raise ValueError("subject is required for email templates")
        return v

    # IMPORTANT: enforce body presence at the model level so field order cannot break it
    @root_validator
    def _require_some_body(cls, values):
        html = (values.get("body_html") or "").strip()
        text = (values.get("body_text") or "").strip()
        if not html and not text:
            raise ValueError("provide body_html and/or body_text")
        # normalize empty strings to None
        values["body_html"] = html or None
        values["body_text"] = text or None
        return values


class TemplateUpdateIn(BaseModel):
    key: Optional[str] = Field(None, min_length=2, max_length=64)
    channel: Optional[Channel] = None
    tag: Optional[Tag] = None
    step_number: Optional[int] = Field(None, ge=0, le=99)
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    subject: Optional[str] = Field(None, max_length=255)
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    is_active: Optional[bool] = None

    @validator("subject")
    def subject_required_if_email(cls, v, values):
        if v is None:
            return v
        ch = values.get("channel")
        if ch == "email" and v.strip() == "":
            raise ValueError("subject cannot be empty for email templates")
        return v


class PreviewIn(BaseModel):
    channel: Channel
    subject: Optional[str] = None
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    @validator("subject", always=True)
    def require_subject_for_email(cls, v, values):
        if values.get("channel") == "email" and not v:
            raise ValueError("subject is required for email preview")
        return v

    # Same root-level enforcement to avoid field-order issues
    @root_validator
    def _require_preview_body(cls, values):
        html = (values.get("body_html") or "").strip()
        text = (values.get("body_text") or "").strip()
        if not html and not text:
            raise ValueError("provide body_html and/or body_text")
        values["body_html"] = html or None
        values["body_text"] = text or None
        return values


class PreviewOut(BaseModel):
    subject: Optional[str]
    body_html: Optional[str]
    body_text: Optional[str]


# ----------------------------- Helpers -----------------------------

def _query_base(db: Session, user_id: int):
    return db.execute(
        select(ReminderTemplate).where(ReminderTemplate.user_id == user_id)
    )

def _load_one(db: Session, user_id: int, template_id: int) -> ReminderTemplate:
    row = db.execute(
        select(ReminderTemplate)
        .where(ReminderTemplate.user_id == user_id, ReminderTemplate.id == template_id)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return row

def _apply_filters(stmt, channel: Optional[str], tag: Optional[str], active: Optional[bool], q: Optional[str]):
    conditions = []
    if channel:
        conditions.append(ReminderTemplate.channel == channel)
    if tag:
        conditions.append(ReminderTemplate.tag == tag)
    if active is not None:
        conditions.append(ReminderTemplate.is_active == active)
    if q:
        like = f"%{q.strip()}%"
        conditions.append(or_(
            ReminderTemplate.key.ilike(like),
            ReminderTemplate.name.ilike(like),
            func.cast(ReminderTemplate.step_number, String).ilike(like) if hasattr(ReminderTemplate, "step_number") else False,
        ))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    return stmt


# ------------------------------ Routes -----------------------------

@router.get("", response_model=List[TemplateOut])
def list_templates(
    channel: Optional[Channel] = Query(None),
    tag: Optional[Tag] = Query(None),
    active: Optional[bool] = Query(None),
    q: Optional[str] = Query(None, description="Search name/key"),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    stmt = select(ReminderTemplate).where(ReminderTemplate.user_id == user.id)
    stmt = _apply_filters(stmt, channel, tag, active, q)
    # Sort by tag, then step_number (NULLs last), then name
    stmt = stmt.order_by(
        ReminderTemplate.tag.asc(),
        func.isnull(ReminderTemplate.step_number).asc(),  # MySQL NULLS LAST
        ReminderTemplate.step_number.asc(),
        ReminderTemplate.name.asc(),
    )
    rows = db.execute(stmt).scalars().all()
    return rows


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = _load_one(db, user.id, template_id)
    return row


@router.post("", response_model=TemplateOut)
def create_template(
    body: TemplateCreateIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    rec = ReminderTemplate(
        user_id=user.id,
        key=body.key.strip(),
        channel=body.channel,
        tag=body.tag,
        step_number=body.step_number,
        name=body.name.strip(),
        subject=(body.subject.strip() if body.subject else None),
        body_html=body.body_html,
        body_text=body.body_text,
        is_active=body.is_active,
        updated_at=datetime.utcnow(),
    )
    db.add(rec)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="A template with this key already exists") from e

    db.refresh(rec)
    return rec


@router.patch("/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: int,
    body: TemplateUpdateIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    rec = _load_one(db, user.id, template_id)

    # Apply provided fields only
    if body.key is not None:
        rec.key = body.key.strip()
    if body.channel is not None:
        rec.channel = body.channel
    if body.tag is not None:
        rec.tag = body.tag
    if body.step_number is not None:
        rec.step_number = body.step_number
    if body.name is not None:
        rec.name = body.name.strip()
    if body.subject is not None:
        rec.subject = body.subject.strip() if body.subject else None
    if body.body_html is not None:
        rec.body_html = body.body_html
    if body.body_text is not None:
        rec.body_text = body.body_text
    if body.is_active is not None:
        rec.is_active = body.is_active

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate key for this user") from e

    db.refresh(rec)
    return rec


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    soft: bool = Query(True, description="Soft delete by setting is_active=false"),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    rec = _load_one(db, user.id, template_id)
    if soft:
        rec.is_active = False
        rec.updated_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "soft_deleted": True}
    else:
        db.delete(rec)
        db.commit()
        return {"ok": True, "deleted": True}


@router.post("/preview", response_model=PreviewOut)
def preview_template(
    body: PreviewIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    if JinjaTemplate is None:
        raise HTTPException(status_code=503, detail="Preview unavailable: jinja2 not installed")

    def render(s: Optional[str]) -> Optional[str]:
        if not s:
            return s
        try:
            return JinjaTemplate(s).render(**body.data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Template render error: {e}")

    return PreviewOut(
        subject=render(body.subject),
        body_html=render(body.body_html),
        body_text=render(body.body_text),
    )

@router.get("/summary")
def summary(
    channel: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    # be lenient with input (empty/undefined -> 'email')
    ch = (channel or "email").strip().lower()
    if ch not in ("email", "sms"):
        ch = "email"

    # COUNT only active rows for each built-in tag (MySQL-safe)
    cnt_expr = func.sum(case((ReminderTemplate.is_active == True, 1), else_=0))  # noqa: E712
    rows = db.execute(
        select(
            ReminderTemplate.tag,
            cnt_expr.label("cnt"),
            func.max(ReminderTemplate.updated_at).label("updated_at"),
        )
        .where(
            ReminderTemplate.user_id == user.id,
            ReminderTemplate.channel == ch,
            ReminderTemplate.tag.in_(("gentle", "firm", "aggressive")),
        )
        .group_by(ReminderTemplate.tag)
    ).all()

    out = {k: {"steps": 0, "updated_at": None} for k in ("gentle", "firm", "aggressive")}
    for tag, cnt, ts in rows:
        out[tag] = {"steps": int(cnt or 0), "updated_at": ts}

    custom_cnt = db.execute(
        select(func.count())
        .where(
            ReminderTemplate.user_id == user.id,
            ReminderTemplate.channel == ch,
            ReminderTemplate.tag == "custom",
            ReminderTemplate.is_active == True,  # noqa: E712
        )
    ).scalar() or 0

    return {"cycles": out, "custom_active": int(custom_cnt)}

@router.post("/duplicate_cycle")
def duplicate_cycle(
    from_tag: Tag = Query(..., description="gentle|firm|aggressive"),
    channel: Channel = Query("email"),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    # load source templates in order
    src = (db.query(ReminderTemplate)
             .filter(ReminderTemplate.user_id == user.id,
                     ReminderTemplate.channel == channel,
                     ReminderTemplate.tag == from_tag)
             .order_by(ReminderTemplate.step_number.asc(), ReminderTemplate.id.asc())
             .all())
    if not src:
        raise HTTPException(404, f"No templates found for tag={from_tag}")

    # copy each into tag=custom; key prefix; avoid collisions
    now = datetime.utcnow()
    created = 0
    for r in src:
        base_key = f"custom_{r.key}"
        key = base_key
        # disambiguate if needed (rare)
        i = 1
        while db.query(ReminderTemplate).filter_by(user_id=user.id, key=key, channel=channel).first():
            i += 1
            key = f"{base_key}_{i}"

        db.add(ReminderTemplate(
            user_id=user.id,
            key=key,
            channel=channel,
            tag="custom",
            step_number=r.step_number,
            name=f"Custom — {r.name}",
            subject=r.subject,
            body_html=r.body_html,
            body_text=r.body_text,
            is_active=True,
            updated_at=now
        ))
        created += 1

    if created:
        db.commit()

    return {"ok": True, "created": created}

# --- Compatibility endpoints for the new Cycles UI ---

@router.get("/cycles")
def cycles_summary(
    active: Optional[bool] = Query(True),
    channel: Channel = Query("email"),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    Returns: [{"tag":"gentle","count":N,"max_step":S}, ...]
    """
    rows = (
        db.query(
            ReminderTemplate.tag,
            func.count(ReminderTemplate.id),
            func.max(ReminderTemplate.step_number),
        )
        .filter(
            ReminderTemplate.user_id == user.id,
            ReminderTemplate.channel == channel,
            (ReminderTemplate.is_active == active) if active is not None else True,
        )
        .group_by(ReminderTemplate.tag)
        .all()
    )

    # normalize to known tags so empty cycles are shown too
    known = ["gentle", "firm", "aggressive"]
    out_map = {t: {"tag": t, "count": 0, "max_step": None} for t in known}
    for tag, cnt, max_step in rows:
        if tag in out_map:
            out_map[tag] = {
                "tag": tag,
                "count": int(cnt or 0),
                "max_step": int(max_step) if max_step is not None else None,
            }
    return list(out_map.values())


@router.get("/by_tag/{tag}", response_model=List[TemplateOut])
def list_by_tag(
    tag: Tag,
    channel: Channel = Query("email"),
    active: Optional[bool] = Query(True),
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    """
    List templates for a single cycle (used by the preview modal).
    """
    stmt = (
        select(ReminderTemplate)
        .where(
            ReminderTemplate.user_id == user.id,
            ReminderTemplate.channel == channel,
            ReminderTemplate.tag == tag,
            (ReminderTemplate.is_active == active) if active is not None else True,
        )
        .order_by(ReminderTemplate.step_number.asc(), ReminderTemplate.id.asc())
    )
    return db.execute(stmt).scalars().all()
