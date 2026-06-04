import importlib

import pytest

from api.app.postmark_safety import (
    apply_staging_email_safety,
    outbox_dry_run_enabled,
    build_postmark_server_create_payload,
    resolve_postmark_delivery_type,
)




def _import_or_skip(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        pytest.skip(f"{module_name} cannot be imported in this test environment: {exc}")

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



def test_server_create_payload_helper_contains_sandbox_in_staging(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    payload = build_postmark_server_create_payload("staging-server")

    assert payload["DeliveryType"] == "Sandbox"


def test_server_create_payload_helper_preserves_live_default(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "live")

    payload = build_postmark_server_create_payload("live-server")

    assert "DeliveryType" not in payload

def test_staging_rejects_explicit_live_delivery_type(monkeypatch):
    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("POSTMARK_DELIVERY_TYPE", "Live")

    with pytest.raises(ValueError, match="requires POSTMARK_DELIVERY_TYPE=Sandbox"):
        resolve_postmark_delivery_type()


def test_staging_webhook_url_resolves_to_staging(monkeypatch):
    from api.app.postmark_safety import resolve_postmark_webhook_url

    _clear_postmark_env(monkeypatch)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("POSTMARK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "staging")

    assert resolve_postmark_webhook_url() == "https://staging.remindandpay.com/api/postmark/webhook"


def test_postmark_server_creation_payload_contains_sandbox_in_staging(monkeypatch):
    postmark_servers = _import_or_skip("api.app.routers.postmark_servers")

    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    calls = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"ID": 123, "ApiTokens": ["server-token"]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return Resp()

    monkeypatch.setattr(postmark_servers.requests, "post", fake_post)

    postmark_servers._create_server("account-token", "server-name")

    assert calls[0][1]["DeliveryType"] == "Sandbox"


def test_postmark_server_creation_payload_omits_delivery_type_in_live(monkeypatch):
    postmark_servers = _import_or_skip("api.app.routers.postmark_servers")

    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "live")
    calls = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"ID": 123, "ApiTokens": ["server-token"]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return Resp()

    monkeypatch.setattr(postmark_servers.requests, "post", fake_post)

    postmark_servers._create_server("account-token", "server-name")

    assert "DeliveryType" not in calls[0][1]


def test_send_via_postmark_applies_staging_override_and_prefix_before_provider(monkeypatch):
    mailer = _import_or_skip("api.app.mailer")

    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("EMAIL_RECIPIENT_OVERRIDE", "admin@remindandpay.com")
    monkeypatch.setenv("EMAIL_SUBJECT_PREFIX", "[STAGING]")
    sent_payloads = []

    class Resp:
        status_code = 200

        def json(self):
            return {"MessageID": "msg-1"}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent_payloads.append(json)
        return Resp()

    monkeypatch.setattr(mailer.requests, "post", fake_post)

    res = mailer.send_via_postmark(
        "sandbox-token",
        "Sender <sender@example.com>",
        "customer@example.net",
        "Invoice due",
        "<p>body</p>",
    )

    assert res.ok is True
    assert sent_payloads[0]["To"] == "admin@remindandpay.com"
    assert sent_payloads[0]["Subject"] == "[STAGING] Invoice due"


def test_send_via_postmark_staging_without_safety_fails_before_provider(monkeypatch):
    mailer = _import_or_skip("api.app.mailer")

    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    def fake_post(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("provider called despite staging fail-closed safety")

    monkeypatch.setattr(mailer.requests, "post", fake_post)

    res = mailer.send_via_postmark(
        "sandbox-token",
        "Sender <sender@example.com>",
        "customer@example.net",
        "Invoice due",
        "<p>body</p>",
    )

    assert res.ok is False
    assert res.permanent is True
    assert "Staging email safety blocked send" in res.error


def test_outbox_dry_run_process_once_prevents_provider_calls(monkeypatch):
    outbox_worker = _import_or_skip("api.app.routers.outbox_worker")

    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("OUTBOX_DRY_RUN", "1")

    class FakeJob:
        id = 44
        channel = "email"
        to_email = "customer@example.net"
        subject = "Invoice"
        template = "statement"
        run_id = None
        lock_owner = "sender-1"
        lock_acquired_at = None
        status = "processing"
        last_error = None
        updated_at = None

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def count(self):
            return 1

    class FakeDB:
        def __init__(self):
            self.job = FakeJob()
            self.commits = 0

        def execute(self, *args, **kwargs):
            return object()

        def query(self, *args, **kwargs):
            return FakeQuery()

        def commit(self):
            self.commits += 1

        def close(self):
            pass

    fake_db = FakeDB()
    claimed = {"done": False}

    def fake_claim(db):
        if claimed["done"]:
            return None
        claimed["done"] = True
        return fake_db.job

    def fail_send(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("provider send called during OUTBOX_DRY_RUN")

    monkeypatch.setattr(outbox_worker, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(outbox_worker, "_requeue_stale_processing", lambda db, stale_seconds=120: 0)
    monkeypatch.setattr(outbox_worker, "_claim_one_due_job", fake_claim)
    monkeypatch.setattr(outbox_worker, "send_via_postmark", fail_send)
    monkeypatch.setattr(outbox_worker, "send_statement_for_user", fail_send)
    monkeypatch.setattr(outbox_worker, "send_chasing_for_user", fail_send)

    assert outbox_worker.process_once() == 0
    assert fake_db.job.status == "canceled"
    assert fake_db.job.last_error == "staging_outbox_dry_run"


def test_initial_setup_uses_sandbox_delivery_type_in_staging(monkeypatch):
    initial_user_setup = _import_or_skip("api.app.initial_user_setup")

    _clear_postmark_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("POSTMARK_ACCOUNT_TOKEN", "account-token")
    monkeypatch.setenv("EMAIL_RECIPIENT_OVERRIDE", "admin@remindandpay.com")
    calls = []

    class Row:
        email = "new@example.com"
        postmark_server_id = None
        postmark_server_token_enc = None

    class FakeDB:
        def execute(self, statement, params=None):
            text = str(statement)
            if "SELECT email FROM users" in text:
                return self
            if "SELECT postmark_server_id, postmark_server_token_enc" in text:
                return EmptyResult()
            return self

        def first(self):
            return Row()

        def commit(self):
            pass

    class EmptyResult:
        def first(self):
            return None

    class Resp:
        status_code = 200
        ok = True
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        if url.endswith("/servers"):
            return Resp({"ID": 123, "ApiTokens": ["server-token"]})
        return Resp({})

    def fake_get(url, headers=None, timeout=None):
        return Resp([])

    monkeypatch.setattr(initial_user_setup.requests, "post", fake_post)
    monkeypatch.setattr(initial_user_setup.requests, "get", fake_get)
    monkeypatch.setattr(initial_user_setup, "encrypt_secret", lambda value: f"enc:{value}")

    result = initial_user_setup._create_postmark_server_and_save(FakeDB(), user_id=7)

    assert result["ok"] is True
    assert calls[0][1]["DeliveryType"] == "Sandbox"
