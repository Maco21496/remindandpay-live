import pytest

from api.app.postmark_safety import (
    apply_staging_email_safety,
    outbox_dry_run_enabled,
    resolve_postmark_delivery_type,
)


POSTMARK_ENV_KEYS = [
    "APP_ENV",
    "ENVIRONMENT",
    "POSTMARK_DELIVERY_TYPE",
    "EMAIL_RECIPIENT_OVERRIDE",
    "EMAIL_SUBJECT_PREFIX",
    "EMAIL_ALLOWED_RECIPIENTS",
    "EMAIL_ALLOWED_DOMAINS",
    "OUTBOX_DRY_RUN",
]


def _clear_postmark_env(monkeypatch):
    for key in POSTMARK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_staging_resolves_delivery_type_sandbox(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    assert resolve_postmark_delivery_type() == "Sandbox"


def test_live_does_not_accidentally_resolve_sandbox(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "live")

    assert resolve_postmark_delivery_type() is None


def test_explicit_delivery_type_accepts_only_live_or_sandbox(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("POSTMARK_DELIVERY_TYPE", "Sandbox")
    assert resolve_postmark_delivery_type() == "Sandbox"

    monkeypatch.setenv("POSTMARK_DELIVERY_TYPE", "not-real")
    with pytest.raises(ValueError, match="POSTMARK_DELIVERY_TYPE"):
        resolve_postmark_delivery_type()


def test_staging_recipient_override_rewrites_to_and_prefixes_subject(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("EMAIL_RECIPIENT_OVERRIDE", "admin@remindandpay.com")
    monkeypatch.setenv("EMAIL_SUBJECT_PREFIX", "[STAGING]")

    safe_to, safe_subject = apply_staging_email_safety("Customer <customer@example.com>", "Invoice due")

    assert safe_to == "admin@remindandpay.com"
    assert safe_subject == "[STAGING] Invoice due"


def test_staging_without_override_or_allow_list_fails_closed(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValueError, match="Staging email safety blocked send"):
        apply_staging_email_safety("customer@example.com", "Invoice due")


def test_staging_allow_list_permits_configured_recipients_and_domains(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("EMAIL_ALLOWED_RECIPIENTS", "allowed@example.com")
    monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "remindandpay.com")

    assert apply_staging_email_safety("allowed@example.com", "Subject") == ("allowed@example.com", "Subject")
    assert apply_staging_email_safety("admin@remindandpay.com", "Subject") == ("admin@remindandpay.com", "Subject")

    with pytest.raises(ValueError, match="non-allowed recipient"):
        apply_staging_email_safety("blocked@example.net", "Subject")


def test_outbox_dry_run_flag_is_available_for_worker_provider_send_guard(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("OUTBOX_DRY_RUN", "1")
    assert outbox_dry_run_enabled() is True

    monkeypatch.setenv("OUTBOX_DRY_RUN", "0")
    assert outbox_dry_run_enabled() is False
