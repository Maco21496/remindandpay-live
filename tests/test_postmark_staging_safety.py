import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from api.app.postmark_safety import (
    apply_staging_email_safety,
    outbox_dry_run_enabled,
    postmark_server_creation_payload,
    resolve_postmark_delivery_type,
    resolve_postmark_inbound_webhook_url,
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
    "APP_BASE_URL",
    "POSTMARK_INBOUND_WEBHOOK_URL",
    "POSTMARK_ACCOUNT_TOKEN",
    "POSTMARK_WEBHOOK_URL",
    "POSTMARK_WEBHOOK_USER",
    "POSTMARK_WEBHOOK_PASS",
]


def _clear_postmark_env(monkeypatch):
    for key in POSTMARK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _load_initial_user_setup_with_fakes(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlalchemy", types.SimpleNamespace(text=lambda value: value))
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm", types.SimpleNamespace(Session=object))
    monkeypatch.setitem(
        sys.modules,
        "api.app.services.statement_globals_logic",
        types.SimpleNamespace(ensure_global_rules=lambda *args, **kwargs: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.app.models",
        types.SimpleNamespace(ReminderTemplate=object, ChasingPlan=object, ChasingTrigger=object),
    )
    sys.modules.pop("api.app.initial_user_setup", None)
    return importlib.import_module("api.app.initial_user_setup")


class _DbResult:
    def __init__(self, value=None):
        self.value = value

    def first(self):
        return self.value


class _CreatePostmarkServerDb:
    def __init__(self):
        self.results = [
            SimpleNamespace(email="customer@example.com"),
            None,
        ]
        self.committed = False

    def execute(self, *args, **kwargs):
        if self.results:
            return _DbResult(self.results.pop(0))
        return _DbResult()

    def commit(self):
        self.committed = True


class _PostmarkCreateServerResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"ID": 123, "ApiTokens": ["server-token"]}


def test_staging_app_base_url_resolves_inbound_webhook_url(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "https://staging.remindandpay.com")

    assert resolve_postmark_inbound_webhook_url() == "https://staging.remindandpay.com/api/postmark/inbound"


def test_live_app_base_url_resolves_inbound_webhook_url(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "https://app.remindandpay.com")

    assert resolve_postmark_inbound_webhook_url() == "https://app.remindandpay.com/api/postmark/inbound"


def test_explicit_inbound_webhook_url_overrides_app_base_url(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "https://staging.remindandpay.com")
    monkeypatch.setenv("POSTMARK_INBOUND_WEBHOOK_URL", "https://hooks.example.test/postmark/inbound")

    assert resolve_postmark_inbound_webhook_url() == "https://hooks.example.test/postmark/inbound"


def test_missing_app_base_url_defaults_inbound_webhook_url_to_live(monkeypatch):
    _clear_postmark_env(monkeypatch)

    assert resolve_postmark_inbound_webhook_url() == "https://app.remindandpay.com/api/postmark/inbound"


def test_missing_postmark_webhook_url_does_not_report_webhook_ensured(monkeypatch):
    initial_user_setup = _load_initial_user_setup_with_fakes(monkeypatch)
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("POSTMARK_ACCOUNT_TOKEN", "account-token")
    monkeypatch.setattr(initial_user_setup, "encrypt_secret", lambda value: f"enc:{value}")

    post_urls = []

    def fake_post(url, *, headers, json, timeout):
        post_urls.append(url)
        assert url == "https://api.postmarkapp.com/servers"
        return _PostmarkCreateServerResponse()

    monkeypatch.setattr(initial_user_setup.requests, "post", fake_post)

    result = initial_user_setup._create_postmark_server_and_save(_CreatePostmarkServerDb(), user_id=7)

    assert result["ok"] is True
    assert "POSTMARK_WEBHOOK_URL is missing" in result["message"]
    assert "webhook ensured" not in result["message"]
    assert post_urls == ["https://api.postmarkapp.com/servers"]


def test_staging_resolves_delivery_type_sandbox(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    assert resolve_postmark_delivery_type() == "Sandbox"


def test_live_default_resolves_delivery_type_live(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "live")

    assert resolve_postmark_delivery_type() == "Live"


def test_explicit_delivery_type_accepts_only_live_or_sandbox(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("POSTMARK_DELIVERY_TYPE", "Sandbox")
    assert resolve_postmark_delivery_type() == "Sandbox"

    monkeypatch.setenv("POSTMARK_DELIVERY_TYPE", "not-real")
    with pytest.raises(ValueError, match="POSTMARK_DELIVERY_TYPE"):
        resolve_postmark_delivery_type()


def test_staging_payload_uses_sandbox(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    assert postmark_server_creation_payload("rp-test") == {
        "Name": "rp-test",
        "Color": "Blue",
        "SmtpApiActivated": True,
        "DeliveryType": "Sandbox",
    }


def test_live_default_payload_uses_live(monkeypatch):
    _clear_postmark_env(monkeypatch)

    assert postmark_server_creation_payload("rp-test") == {
        "Name": "rp-test",
        "Color": "Blue",
        "SmtpApiActivated": True,
        "DeliveryType": "Live",
    }


def test_explicit_sandbox_payload(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("POSTMARK_DELIVERY_TYPE", "Sandbox")

    assert postmark_server_creation_payload("rp-test") == {
        "Name": "rp-test",
        "Color": "Blue",
        "SmtpApiActivated": True,
        "DeliveryType": "Sandbox",
    }


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
