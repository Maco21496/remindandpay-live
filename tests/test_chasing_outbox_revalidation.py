from types import SimpleNamespace
import json

from api.app.routers.chasing_reminders import _allowed_channels
from api.app.services.chasing_outbox_revalidation import revalidate_chasing_sms_outbox


class _ExecResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeDb:
    def __init__(self, *, customer=True, sms_enabled=True, delivery_mode="sms", rule=True, overdue=True):
        self.executed_sql = []
        self.customer = customer
        self.sms_enabled = sms_enabled
        self.delivery_mode = delivery_mode
        self.rule = rule
        self.overdue = overdue

    def execute(self, sql, params):
        assert sql.__class__.__name__ == "TextClause", f"Expected TextClause, got {type(sql)!r}"
        self.executed_sql.append(str(sql))
        sql = " ".join(str(sql).split()).lower()
        if "from customers" in sql:
            return _ExecResult((1,) if self.customer else None)
        if "from account_sms_settings" in sql:
            return _ExecResult(SimpleNamespace(enabled=1 if self.sms_enabled else 0, chasing_delivery_mode=self.delivery_mode))
        if "from reminder_rules" in sql:
            return _ExecResult((1,) if self.rule else None)
        if "from invoices i" in sql:
            return _ExecResult((1,) if self.overdue else None)
        return _ExecResult(None)


def _row(payload, invoice_id=None):
    return SimpleNamespace(user_id=7, payload_json=payload, invoice_id=invoice_id)


def test_valid_chasing_sms_passes():
    payload = {
        "eligibility_kind": "chasing",
        "customer_id": 1,
        "sequence_id": 2,
        "step_id": 3,
        "rule_id": 10,
        "channel": "sms",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "supersession_key": "customer:1:channel:sms:sequence:2",
        "invoice_id_at_render": 99,
    }
    res = revalidate_chasing_sms_outbox(_FakeDb(), _row(payload))
    assert res.valid_to_send is True
    assert res.reason == "valid"


def test_missing_metadata_fails_conservatively():
    res = revalidate_chasing_sms_outbox(_FakeDb(), _row({"eligibility_kind": "chasing"}))
    assert res.valid_to_send is False
    assert res.reason == "missing_context"


def test_paid_or_not_overdue_fails():
    payload = {
        "eligibility_kind": "chasing",
        "customer_id": 1,
        "sequence_id": 2,
        "step_id": 3,
        "rule_id": 10,
        "channel": "sms",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "supersession_key": "customer:1:channel:sms:sequence:2",
    }
    res = revalidate_chasing_sms_outbox(_FakeDb(overdue=False), _row(payload))
    assert res.valid_to_send is False
    assert res.reason == "no_longer_overdue"


def test_manual_send_sms_ignores_automatic_delivery_mode_email():
    payload = {
        "eligibility_kind": "chasing",
        "customer_id": 1,
        "sequence_id": 2,
        "step_id": 3,
        "rule_id": 10,
        "channel": "sms",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "supersession_key": "customer:1:channel:sms:sequence:2",
    }
    payload["source"] = "manual_send"
    res = revalidate_chasing_sms_outbox(_FakeDb(sms_enabled=True, delivery_mode="email"), _row(payload))
    assert res.valid_to_send is True
    assert res.reason == "valid"


def test_row_559_string_payload_is_not_missing_context():
    payload = {
        "eligibility_kind": "chasing",
        "user_id": 11,
        "customer_id": 10,
        "invoice_id_at_render": 86,
        "oldest_days_overdue_at_render": 178,
        "generated_at_utc": "2026-05-28T12:00:20Z",
        "sequence_id": 11,
        "step_id": 44,
        "rule_id": 36,
        "channel": "sms",
        "content_hash": "x",
        "supersession_key": "customer:10:channel:sms:sequence:11",
        "summary": {"invoice_count": 1, "overdue_total": "120.00", "oldest_days_overdue": 178},
    }
    row = SimpleNamespace(user_id=11, customer_id=10, rule_id=36, invoice_id=86, payload_json=json.dumps(payload))
    res = revalidate_chasing_sms_outbox(_FakeDb(), row)
    assert res.valid_to_send is True
    assert res.reason == "valid"


def test_string_payload_reaches_db_checks_without_missing_context():
    payload = {
        "eligibility_kind": "chasing",
        "user_id": 11,
        "customer_id": 10,
        "invoice_id_at_render": 86,
        "oldest_days_overdue_at_render": 178,
        "generated_at_utc": "2026-05-28T12:00:20Z",
        "sequence_id": 11,
        "step_id": 44,
        "rule_id": 36,
        "channel": "sms",
        "content_hash": "x",
        "supersession_key": "customer:10:channel:sms:sequence:11",
        "summary": {"invoice_count": 1, "overdue_total": "120.00", "oldest_days_overdue": 178},
    }
    db = _FakeDb()
    row = SimpleNamespace(user_id=11, customer_id=10, rule_id=36, invoice_id=86, payload_json=json.dumps(payload))

    res = revalidate_chasing_sms_outbox(db, row)

    assert res.valid_to_send is True
    assert res.reason == "valid"
    assert len(db.executed_sql) >= 4



def test_sms_account_disabled_still_blocks_sms():
    payload = {
        "eligibility_kind": "chasing",
        "source": "manual_send",
        "customer_id": 1,
        "sequence_id": 2,
        "step_id": 3,
        "rule_id": 10,
        "channel": "sms",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "supersession_key": "customer:1:channel:sms:sequence:2",
    }
    res = revalidate_chasing_sms_outbox(_FakeDb(sms_enabled=False, delivery_mode="sms"), _row(payload))
    assert res.valid_to_send is False
    assert res.reason == "sms_account_disabled"


def test_scheduled_enqueue_delivery_mode_email_does_not_include_sms():
    assert _allowed_channels("email", sms_enabled=True) == ["email"]


def test_scheduled_enqueue_delivery_mode_both_includes_sms_when_enabled():
    assert _allowed_channels("both", sms_enabled=True) == ["email", "sms"]

