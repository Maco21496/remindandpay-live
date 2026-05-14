# FINAL VERSION OF api/app/routers/admin_app.py
from typing import List
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..models import SmsWebhookLog, User, BillingSettings, AccountBillingProfile
from ..models import EmailOutbox
from ..shared import templates
from .auth import require_owner

router = APIRouter(prefix="/admin", tags=["admin"])


def _render_admin_dashboard(request: Request, db: Session, owner: User):
    """
    Owner-only management screen for all users.
    Shows basic info and allows pausing / unpausing / deactivating accounts.
    """
    users: List[User] = (
        db.query(User)
          .order_by(User.created_at.desc())
          .all()
    )

    active_tab = request.query_params.get("tab", "users")

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "users": users,
            "owner_email": (owner.email or "").strip().lower(),
            "active_tab": active_tab,
        },
    )


@router.get("/users", response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    return _render_admin_dashboard(request=request, db=db, owner=owner)


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard_page(
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    return _render_admin_dashboard(request=request, db=db, owner=owner)


@router.post("/users/{user_id}/pause")
def admin_pause_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    Soft-pause a user: sets is_active = 0.
    User will not be able to log in.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Never allow pausing the owner account
    if (target.email or "").strip().lower() == "admin@remindandpay.com":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot modify owner account")

    target.is_active = False
    db.add(target)
    db.commit()

    return RedirectResponse(url="/admin/dashboard?tab=users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/unpause")
def admin_unpause_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    Unpause a user: sets is_active = 1.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.is_active = True
    db.add(target)
    db.commit()

    return RedirectResponse(url="/admin/dashboard?tab=users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/deactivate")
def admin_deactivate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    "Delete" action implemented as a soft deactivation using is_active = 0.
    This avoids problems with foreign-key references in other tables.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Never allow deleting the owner account
    if (target.email or "").strip().lower() == "admin@remindandpay.com":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot modify owner account")

    target.is_active = False
    db.add(target)
    db.commit()

    return RedirectResponse(url="/admin/dashboard?tab=users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/sms_webhooks")
def admin_sms_webhooks(
    limit: int = 100,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    limit = max(1, min(500, int(limit or 100)))
    logs = (
        db.query(SmsWebhookLog)
        .order_by(SmsWebhookLog.created_at.desc(), SmsWebhookLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "logs": [
            {
                "id": log.id,
                "created_at": log.created_at,
                "kind": log.kind,
                "account_sid": log.account_sid,
                "message_sid": log.message_sid,
                "payload": log.payload,
            }
            for log in logs
        ]
    }


@router.get("/notifications/templates")
def admin_notification_templates(
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    rows = db.execute(
        text(
            """
            SELECT id, event_key, channel, enabled, subject_template, body_template,
                   from_email, from_name, cooldown_minutes, updated_at
            FROM app_notification_templates
            ORDER BY event_key ASC
            """
        )
    ).mappings().all()
    return {"templates": [dict(r) for r in rows]}


@router.get("/notifications/triggers")
def admin_notification_triggers(
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    rows = db.execute(
        text(
            """
            SELECT id, event_key, enabled, trigger_type, threshold_value, threshold_unit, updated_at
            FROM app_notification_triggers
            ORDER BY event_key ASC
            """
        )
    ).mappings().all()
    return {"triggers": [dict(r) for r in rows]}


@router.post("/notifications/triggers/{event_key}")
def admin_update_notification_trigger(
    event_key: str,
    payload: dict,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    allowed = {"enabled", "trigger_type", "threshold_value", "threshold_unit"}
    updates = {k: payload.get(k) for k in allowed if k in payload}
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")

    sets = []
    params = {"event_key": event_key}
    for key, value in updates.items():
        sets.append(f"{key} = :{key}")
        params[key] = value
    sets.append("updated_at = CURRENT_TIMESTAMP")
    q = f"UPDATE app_notification_triggers SET {', '.join(sets)} WHERE event_key = :event_key"
    res = db.execute(text(q), params)
    if (res.rowcount or 0) == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="Trigger not found")
    db.commit()
    return {"ok": True}


@router.post("/notifications/templates/{template_id}")
def admin_update_notification_template(
    template_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    allowed = {
        "enabled",
        "subject_template",
        "body_template",
        "from_email",
        "from_name",
        "cooldown_minutes",
    }
    updates = {k: payload.get(k) for k in allowed if k in payload}
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")

    sets = []
    params = {"id": template_id}
    for key, value in updates.items():
        sets.append(f"{key} = :{key}")
        params[key] = value
    sets.append("updated_at = CURRENT_TIMESTAMP")

    q = f"UPDATE app_notification_templates SET {', '.join(sets)} WHERE id = :id"
    res = db.execute(text(q), params)
    if (res.rowcount or 0) == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="Template not found")
    db.commit()
    return {"ok": True}


@router.get("/notifications/log")
def admin_notification_log(
    limit: int = 100,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    limit = max(1, min(500, int(limit or 100)))
    params = {"limit": limit}
    where = ""
    if status_filter:
        where = "WHERE l.status = :status_filter"
        params["status_filter"] = status_filter
    rows = db.execute(
        text(
            f"""
            SELECT l.id, l.user_id, u.email AS user_email, l.event_key, l.channel, l.status,
                   l.dedupe_key, l.detail, l.created_at
            FROM app_notification_log l
            LEFT JOIN users u ON u.id = l.user_id
            {where}
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return {"logs": [dict(r) for r in rows]}


@router.post("/notifications/templates/{template_id}/test")
def admin_test_notification_template(
    template_id: int,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    payload = payload or {}
    row = db.execute(
        text(
            """
            SELECT event_key, enabled, subject_template, body_template, from_email, from_name
            FROM app_notification_templates
            WHERE id = :id
            LIMIT 1
            """
        ),
        {"id": template_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")

    to_email = (payload.get("to_email") or owner.email or "").strip()
    if not to_email:
        raise HTTPException(status_code=400, detail="No destination email available")

    context = {
        "user_name": "Test User",
        "balance": 123,
        "monthly_cost": 100,
        "days_left": 3,
        "topup_credits": 500,
    }
    subject = row["subject_template"] or ""
    body = row["body_template"] or ""
    for k, v in context.items():
        subject = subject.replace(f"{{{{{k}}}}}", str(v))
        body = body.replace(f"{{{{{k}}}}}", str(v))

    from_email = (row.get("from_email") or os.getenv("NOTIFICATIONS_EMAIL", "")).strip() or None
    from_name = (row.get("from_name") or os.getenv("NOTIFICATIONS_FROM_NAME", "Remind & Pay")).strip()
    dedupe_key = f"test:{row['event_key']}:{owner.id}:{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    db.execute(
        text(
            """
            INSERT INTO app_notification_log (user_id, event_key, channel, dedupe_key, status, detail)
            VALUES (:user_id, :event_key, 'email', :dedupe_key, 'queued', JSON_OBJECT('source','admin_test'))
            """
        ),
        {"user_id": owner.id, "event_key": row["event_key"], "dedupe_key": dedupe_key},
    )
    log_row = db.execute(
        text("SELECT id FROM app_notification_log WHERE dedupe_key = :dedupe_key LIMIT 1"),
        {"dedupe_key": dedupe_key},
    ).mappings().first()
    log_id = log_row["id"] if log_row else None

    db.add(
        EmailOutbox(
            user_id=owner.id,
            customer_id=None,
            invoice_id=None,
            channel="email",
            template=f"app_notification:{row['event_key']}",
            server_scope="default_server",
            to_email=to_email,
            subject=subject,
            body=body,
            payload_json={
                "app_notification": True,
                "event_key": row["event_key"],
                "from_email": from_email,
                "from_name": from_name,
                "is_test": True,
                "notification_log_id": log_id,
            },
            status="queued",
        )
    )
    db.commit()
    return {"ok": True, "queued_to": to_email}


@router.get("/billing/settings")
def admin_get_billing_settings(
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    row = db.query(BillingSettings).order_by(BillingSettings.id.asc()).first()
    if not row:
        row = BillingSettings(default_trial_days=30)
        db.add(row)
        db.commit()
        db.refresh(row)
    return {
        "default_trial_days": int(row.default_trial_days or 30),
        "updated_by_user_id": row.updated_by_user_id,
        "updated_at": row.updated_at,
    }


@router.post("/billing/settings")
def admin_update_billing_settings(
    payload: dict,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    default_trial_days = payload.get("default_trial_days")
    if default_trial_days is None:
        raise HTTPException(status_code=400, detail="default_trial_days is required")
    try:
        default_trial_days = int(default_trial_days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="default_trial_days must be an integer")
    if default_trial_days < 0:
        raise HTTPException(status_code=400, detail="default_trial_days must be >= 0")

    row = db.query(BillingSettings).order_by(BillingSettings.id.asc()).first()
    if not row:
        row = BillingSettings(default_trial_days=default_trial_days, updated_by_user_id=owner.id)
        db.add(row)
    else:
        row.default_trial_days = default_trial_days
        row.updated_by_user_id = owner.id
    db.commit()
    return {"ok": True, "default_trial_days": default_trial_days}


@router.get("/billing/users/{user_id}")
def admin_get_user_billing_profile(
    user_id: int,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    row = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Billing profile not found")
    return {
        "user_id": row.user_id,
        "trial_days_assigned": row.trial_days_assigned,
        "trial_started_at": row.trial_started_at,
        "trial_ends_at": row.trial_ends_at,
        "subscription_status": row.subscription_status,
        "stripe_customer_id": row.stripe_customer_id,
        "stripe_subscription_id": row.stripe_subscription_id,
    }


@router.post("/billing/users/{user_id}")
def admin_update_user_billing_profile(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    row = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Billing profile not found")

    if "trial_days_assigned" in payload:
        try:
            trial_days = int(payload.get("trial_days_assigned"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="trial_days_assigned must be an integer")
        if trial_days < 0:
            raise HTTPException(status_code=400, detail="trial_days_assigned must be >= 0")
        row.trial_days_assigned = trial_days
        row.trial_ends_at = row.trial_started_at + timedelta(days=trial_days)

    if "trial_ends_at" in payload and payload.get("trial_ends_at"):
        row.trial_ends_at = datetime.fromisoformat(str(payload.get("trial_ends_at")))

    if "subscription_status" in payload and payload.get("subscription_status"):
        row.subscription_status = str(payload.get("subscription_status"))

    db.commit()
    return {"ok": True}
