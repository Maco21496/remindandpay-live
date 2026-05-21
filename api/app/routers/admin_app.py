# FINAL VERSION OF api/app/routers/admin_app.py
from typing import List
import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from sqlalchemy.exc import IntegrityError

from ..database import get_db
from ..models import SmsWebhookLog, User, BillingSettings, AccountBillingProfile, SmsCreditLedger
from ..services.billing_trial import enqueue_trial_notifications
from ..models import EmailOutbox
from ..shared import templates, BaseModel
from .auth import require_owner

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


def _topup_anomaly_counts_by_user(db: Session) -> dict[int, int]:
    rows = (
        db.query(SmsCreditLedger.user_id, SmsCreditLedger.details)
        .filter(SmsCreditLedger.reason == "stripe_topup")
        .all()
    )
    counts: dict[int, int] = {}
    for user_id, details in rows:
        meta = details if isinstance(details, dict) else {}
        pi = str(meta.get("payment_intent_id") or "").strip()
        invoice_link = str(meta.get("stripe_invoice_pdf") or meta.get("stripe_invoice_url") or "").strip()

        # Real issues only:
        # 1) No PI reference (payment cannot be verified), or
        # 2) PI exists but we still have no invoice link AND not PI-verified.
        if not pi or (not invoice_link and not meta.get("payment_verified")):
            counts[user_id] = counts.get(user_id, 0) + 1
    return counts


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
    topup_anomaly_counts = _topup_anomaly_counts_by_user(db)

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "users": users,
            "owner_email": (owner.email or "").strip().lower(),
            "active_tab": active_tab,
            "topup_anomaly_counts": topup_anomaly_counts,
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


@router.post("/billing/notifications/enqueue")
def admin_enqueue_billing_trial_notifications(
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    result = enqueue_trial_notifications(db)
    return {"ok": True, **result}


@router.get("/billing/topup-anomalies")
def admin_billing_topup_anomalies(
    limit: int = 200,
    user_id: int | None = None,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    limit = max(1, min(int(limit or 200), 1000))
    q = db.query(SmsCreditLedger).filter(SmsCreditLedger.reason == "stripe_topup")
    if user_id is not None:
        q = q.filter(SmsCreditLedger.user_id == user_id)
    rows = q.order_by(SmsCreditLedger.created_at.desc()).limit(limit).all()

    anomalies = []
    for row in rows:
        details = dict(row.details) if isinstance(row.details, dict) else {}
        status = str(details.get("invoice_reconcile_status") or "pending")
        pi = str(details.get("payment_intent_id") or "").strip()
        # PI is the source of truth: only rows missing PI are anomalies.
        if pi:
            continue
        retry_count = int(details.get("invoice_retry_count") or 0)
        max_attempts = int(details.get("invoice_retry_max_attempts") or 5)
        anomalies.append({
            "ledger_id": row.id,
            "user_id": row.user_id,
            "created_at": row.created_at,
            "amount": row.amount,
            "reference_id": row.reference_id,
            "payment_intent_id": details.get("payment_intent_id"),
            "stripe_session_id": details.get("stripe_session_id"),
            "invoice_reconcile_status": status,
            "invoice_retry_count": retry_count,
            "invoice_retry_max_attempts": max_attempts,
            "next_retry_at": details.get("next_retry_at"),
        })

    return {
        "count": len(anomalies),
        "anomalies": anomalies,
    }



def _retry_delay_for_attempt(attempt: int) -> timedelta:
    # Attempt numbers are 1-indexed for retries after initial insert.
    schedule = {
        1: timedelta(minutes=15),
        2: timedelta(hours=1),
        3: timedelta(hours=4),
        4: timedelta(hours=12),
        5: timedelta(hours=24),
    }
    return schedule.get(attempt, timedelta(hours=24))


def _resolve_topup_invoice_details_admin(stripe_client, payment_intent_id: str, *, include_debug: bool = False) -> dict:
    payment_intent_id = (payment_intent_id or "").strip()
    if not payment_intent_id:
        return {}

    debug = {"list_error": None, "pi_error": None, "charge_error": None, "list_count": 0}
    invoice_id = ""
    inv = None

    try:
        pi = stripe_client.PaymentIntent.retrieve(payment_intent_id)
    except Exception as ex:
        debug["pi_error"] = str(ex)
        pi = None

    if pi is not None:
        invoice_id = str(getattr(getattr(pi, "invoice", None), "id", None) or getattr(pi, "invoice", "") or "").strip()

        if not invoice_id:
            charge_id = str(getattr(getattr(pi, "latest_charge", None), "id", None) or getattr(pi, "latest_charge", "") or "").strip()
            if charge_id:
                try:
                    ch = stripe_client.Charge.retrieve(charge_id)
                    invoice_id = str(getattr(getattr(ch, "invoice", None), "id", None) or getattr(ch, "invoice", "") or "").strip()
                except Exception as ex:
                    debug["charge_error"] = str(ex)

        if not invoice_id:
            customer_id = str(getattr(getattr(pi, "customer", None), "id", None) or getattr(pi, "customer", "") or "").strip()
            if customer_id:
                try:
                    lst = stripe_client.Invoice.list(customer=customer_id, limit=100)
                    items = getattr(lst, "data", None) or []
                    debug["list_count"] = len(items)
                    for candidate in items:
                        candidate_pi = str(getattr(getattr(candidate, "payment_intent", None), "id", None) or getattr(candidate, "payment_intent", "") or "").strip()
                        if candidate_pi == payment_intent_id:
                            inv = candidate
                            invoice_id = str(getattr(inv, "id", "") or "").strip()
                            break

                        # Some Stripe shapes keep PI link only on the charge attached to invoice.
                        candidate_charge = str(getattr(getattr(candidate, "charge", None), "id", None) or getattr(candidate, "charge", "") or "").strip()
                        if candidate_charge:
                            try:
                                ch2 = stripe_client.Charge.retrieve(candidate_charge)
                                ch2_pi = str(getattr(getattr(ch2, "payment_intent", None), "id", None) or getattr(ch2, "payment_intent", "") or "").strip()
                                if ch2_pi == payment_intent_id:
                                    inv = candidate
                                    invoice_id = str(getattr(inv, "id", "") or "").strip()
                                    break
                            except Exception:
                                pass
                except Exception as ex:
                    debug["list_error"] = str(ex)

    if not invoice_id:
        pi_status = str(getattr(pi, "status", "") or "") if pi is not None else ""
        if pi is not None and pi_status == "succeeded":
            result = {
                "invoice_reconcile_status": "verified_no_invoice",
                "payment_verified": True,
                "invoice_retry_count": 0,
                "invoice_retry_max_attempts": 5,
                "next_retry_at": None,
            }
            if include_debug:
                result["debug"] = debug
            return result
        if include_debug:
            return {"debug": debug}
        return {}
    if inv is None:
        try:
            inv = stripe_client.Invoice.retrieve(invoice_id)
        except Exception:
            return {"stripe_invoice_id": invoice_id}
    result = {
        "stripe_invoice_id": invoice_id,
        "stripe_invoice_number": getattr(inv, "number", None),
        "stripe_invoice_pdf": getattr(inv, "invoice_pdf", None),
        "stripe_invoice_url": getattr(inv, "hosted_invoice_url", None),
    }
    if include_debug:
        result["debug"] = debug
    return result


@router.post("/billing/topup-anomalies/reconcile")
def admin_reconcile_topup_anomalies(
    limit: int = 200,
    user_id: int | None = None,
    apply_changes: bool = True,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY is not configured")

    limit = max(1, min(int(limit or 200), 1000))
    q = db.query(SmsCreditLedger).filter(SmsCreditLedger.reason == "stripe_topup")
    if user_id is not None:
        q = q.filter(SmsCreditLedger.user_id == user_id)
    rows = q.order_by(SmsCreditLedger.created_at.desc()).limit(limit).all()

    scanned = 0
    resolved = 0
    anomalies = 0
    updated = 0
    now = datetime.now(timezone.utc)
    debug_enabled = os.getenv("BILLING_RECONCILE_DEBUG", "0").lower() in {"1", "true", "yes"}
    debug_rows = []

    for row in rows:
        details = dict(row.details) if isinstance(row.details, dict) else {}
        status = str(details.get("invoice_reconcile_status") or "pending")
        pi = str(details.get("payment_intent_id") or "").strip()
        # PI is the source of truth: only rows missing PI are anomalies.
        if pi:
            continue
        retry_count = int(details.get("invoice_retry_count") or 0)
        max_attempts = int(details.get("invoice_retry_max_attempts") or 5)

        scanned += 1
        retry_count = int(details.get("invoice_retry_count") or 0)
        max_attempts = int(details.get("invoice_retry_max_attempts") or 5)
        payment_intent_id = str(details.get("payment_intent_id") or "").strip()

        if debug_enabled and len(debug_rows) < 50:
            debug_rows.append({
                "ledger_id": row.id,
                "payment_intent_id": payment_intent_id,
                "found_invoice_id": None,
                "found_invoice_number": None,
                "prev_status": status,
                "prev_retry_count": retry_count,
                "resolver_debug": {"note": "missing_payment_intent_id"},
            })

        retry_count += 1
        details["invoice_retry_count"] = retry_count
        details["invoice_retry_max_attempts"] = max_attempts
        if retry_count >= max_attempts:
            details["invoice_reconcile_status"] = "anomaly"
            details["next_retry_at"] = None
            anomalies += 1
        else:
            details["invoice_reconcile_status"] = "pending"
            details["next_retry_at"] = (now + _retry_delay_for_attempt(retry_count)).isoformat()

        if apply_changes:
            row.details = details
            db.add(row)
            updated += 1

    if apply_changes and updated:
        db.commit()

    if debug_enabled:
        logger.warning("[admin_billing_reconcile_summary] scanned=%s resolved=%s anomalies=%s updated=%s user_id=%s", scanned, resolved, anomalies, updated, user_id)

    return {
        "ok": True,
        "apply_changes": apply_changes,
        "scanned": scanned,
        "resolved": resolved,
        "anomalies": anomalies,
        "updated": updated,
        "debug": debug_rows if debug_enabled else [],
    }



@router.get("/billing/user-invoices")
def admin_user_invoices(
    user_id: int,
    limit: int = 30,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY is not configured")

    profile = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user_id).first()
    if not profile or not profile.stripe_customer_id:
        return {"invoices": []}

    limit = max(1, min(int(limit or 30), 100))
    invoices = stripe.Invoice.list(customer=profile.stripe_customer_id, limit=limit)
    rows = []
    for inv in invoices.auto_paging_iter():
        inv_id = str(getattr(inv, "id", "") or "")
        inv_number = str(getattr(inv, "number", "") or "")
        inv_status = str(getattr(inv, "status", "") or "")
        inv_pi = str(getattr(getattr(inv, "payment_intent", None), "id", None) or getattr(inv, "payment_intent", "") or "")
        rows.append({
            "id": inv_id,
            "number": inv_number,
            "status": inv_status,
            "created": getattr(inv, "created", None),
            "amount_due": getattr(inv, "amount_due", None),
            "currency": getattr(inv, "currency", None),
            "payment_intent_id": inv_pi,
            "hosted_invoice_url": getattr(inv, "hosted_invoice_url", None),
        })

    rows.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return {"invoices": rows[:limit]}


class AdminRefundTopupIn(BaseModel):
    user_id: int
    invoice_id: str
    amount_pence: int | None = None
    reason: str | None = None


@router.post("/billing/refund-topup")
def admin_refund_topup(
    payload: AdminRefundTopupIn,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY is not configured")

    profile = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == payload.user_id).first()
    if not profile or not profile.stripe_customer_id:
        raise HTTPException(status_code=404, detail="Billing profile/customer not found")

    invoice = stripe.Invoice.retrieve(payload.invoice_id)
    inv_customer = str(getattr(getattr(invoice, "customer", None), "id", None) or getattr(invoice, "customer", "") or "").strip()
    if inv_customer != str(profile.stripe_customer_id or "").strip():
        raise HTTPException(status_code=400, detail="Invoice does not belong to requested user")

    pi_id = str(getattr(getattr(invoice, "payment_intent", None), "id", None) or getattr(invoice, "payment_intent", "") or "").strip()
    if not pi_id:
        raise HTTPException(status_code=400, detail="Invoice has no payment_intent")

    refund_kwargs = {"payment_intent": pi_id}
    if payload.amount_pence and int(payload.amount_pence) > 0:
        refund_kwargs["amount"] = int(payload.amount_pence)
    refund = stripe.Refund.create(**refund_kwargs)

    # Create credit note tied to invoice for accounting visibility.
    credit_note_kwargs = {
        "invoice": payload.invoice_id,
        "reason": "requested_by_customer",
        "memo": (payload.reason or "Admin-approved refund").strip()[:500],
    }
    if payload.amount_pence and int(payload.amount_pence) > 0:
        credit_note_kwargs["amount"] = int(payload.amount_pence)
    credit_note = None
    try:
        credit_note = stripe.CreditNote.create(**credit_note_kwargs)
    except Exception:
        # Keep refund successful even if credit note creation is unavailable for this invoice shape.
        credit_note = None

    # Local ledger update mirrors webhook behavior immediately.
    credits = (
        db.query(SmsCreditLedger)
        .filter(SmsCreditLedger.user_id == payload.user_id)
        .filter(SmsCreditLedger.reason == "stripe_topup")
        .all()
    )
    matched = []
    for row in credits:
        details = dict(row.details) if isinstance(row.details, dict) else {}
        if str(details.get("payment_intent_id") or "").strip() == pi_id and row.entry_type == "credit":
            matched.append(row)

    if matched:
        total_credits = sum(int(r.amount or 0) for r in matched)
        amount_refunded = int(getattr(refund, "amount", 0) or 0)
        amount_captured = int(getattr(getattr(invoice, "charge", None), "amount", 0) or 0)
        if amount_captured > 0 and amount_refunded < amount_captured:
            to_reverse = max(1, int((total_credits * amount_refunded) / amount_captured))
        else:
            to_reverse = total_credits

        refund_id = str(getattr(refund, "id", "") or "")
        refund_ref = f"stripe:refund:{refund_id}" if refund_id else f"stripe:refund:pi:{pi_id}:{int(datetime.now(timezone.utc).timestamp())}"

        for credit_row in matched:
            cdetails = dict(credit_row.details) if isinstance(credit_row.details, dict) else {}
            cdetails["refunded"] = True
            cdetails["refunded_at"] = datetime.now(timezone.utc).isoformat()
            cdetails["refund_reference"] = refund_ref
            if credit_note is not None:
                cdetails["credit_note_id"] = str(getattr(credit_note, "id", "") or "")
            credit_row.details = cdetails
            db.add(credit_row)

        db.add(SmsCreditLedger(
            user_id=payload.user_id,
            entry_type="debit",
            amount=to_reverse,
            reason="stripe_refund_reversal",
            reference_id=refund_ref,
            details={
                "source": "admin_refund_api",
                "payment_intent_id": pi_id,
                "stripe_refund_id": str(getattr(refund, "id", "") or ""),
                "stripe_invoice_id": payload.invoice_id,
                "stripe_credit_note_id": str(getattr(credit_note, "id", "") or "") if credit_note is not None else None,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            },
        ))
        db.commit()

    return {
        "ok": True,
        "payment_intent_id": pi_id,
        "refund_id": str(getattr(refund, "id", "") or ""),
        "credit_note_id": str(getattr(credit_note, "id", "") or "") if credit_note is not None else None,
    }
