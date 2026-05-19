from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext

from ..models import AccountBillingProfile, BillingSettings, User


def get_or_create_billing_settings(db: Session) -> BillingSettings:
    row = db.query(BillingSettings).order_by(BillingSettings.id.asc()).first()
    if row:
        return row
    row = BillingSettings(default_trial_days=30)
    db.add(row)
    db.flush()
    return row


def ensure_billing_profile(db: Session, user: User) -> AccountBillingProfile:
    row = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user.id).first()
    if row:
        return row

    settings = get_or_create_billing_settings(db)
    trial_days = max(0, int(settings.default_trial_days or 0))
    trial_start = user.created_at or datetime.utcnow()
    trial_end = trial_start + timedelta(days=trial_days)

    status = "trialing" if trial_days > 0 else "none"
    row = AccountBillingProfile(
        user_id=user.id,
        trial_days_assigned=trial_days,
        trial_started_at=trial_start,
        trial_ends_at=trial_end,
        subscription_status=status,
    )
    db.add(row)
    db.flush()
    return row



def assert_billing_allows_sending(db: Session, user: User) -> None:
    profile = ensure_billing_profile(db, user)
    now = datetime.utcnow()
    status = (profile.subscription_status or "").strip().lower()

    if status == "active":
        return

    in_trial = bool(profile.trial_ends_at and profile.trial_ends_at >= now)
    if status in {"trialing", "none", "trial_expired"} and in_trial:
        return

    raise ValueError("Trial expired. Please activate membership in Settings → Billing to enable sending.")


def enqueue_trial_notifications(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()

    warning_start = now + timedelta(hours=47)
    warning_end = now + timedelta(hours=49)

    warning_rows = db.query(AccountBillingProfile).filter(
        AccountBillingProfile.subscription_status.in_(["trialing", "none", "trial_expired"]),
        AccountBillingProfile.trial_ends_at >= warning_start,
        AccountBillingProfile.trial_ends_at <= warning_end,
    ).all()

    expired_rows = db.query(AccountBillingProfile).filter(
        AccountBillingProfile.subscription_status.in_(["trialing", "none", "trial_expired", "past_due", "canceled"]),
        AccountBillingProfile.trial_ends_at < now,
    ).all()

    queued_warning = 0
    queued_expired = 0

    for row in warning_rows:
        dedupe_key = f"billing_trial_expiring_48h:{row.user_id}:{row.trial_ends_at.date().isoformat()}"
        if _enqueue_notification(db, row.user_id, "billing_trial_expiring_48h", dedupe_key):
            queued_warning += 1

    for row in expired_rows:
        dedupe_key = f"billing_trial_expired:{row.user_id}:{row.trial_ends_at.date().isoformat()}"
        if _enqueue_notification(db, row.user_id, "billing_trial_expired", dedupe_key):
            queued_expired += 1

    db.commit()
    return {"queued_warning": queued_warning, "queued_expired": queued_expired}


def _enqueue_notification(db: Session, user_id: int, event_key: str, dedupe_key: str) -> bool:
    existing = db.execute(
        sqltext("SELECT id FROM app_notification_log WHERE dedupe_key = :d LIMIT 1"),
        {"d": dedupe_key},
    ).first()
    if existing:
        return False

    db.execute(
        sqltext(
            """
            INSERT INTO app_notification_log (user_id, event_key, channel, dedupe_key, status, detail)
            VALUES (:user_id, :event_key, 'email', :dedupe_key, 'queued', JSON_OBJECT('source','billing_trial'))
            """
        ),
        {"user_id": user_id, "event_key": event_key, "dedupe_key": dedupe_key},
    )
    return True
