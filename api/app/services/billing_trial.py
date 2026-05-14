from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

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
