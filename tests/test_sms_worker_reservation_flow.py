from types import SimpleNamespace

import pytest

from api.app.routers import outbox_worker
from api.app.models import SmsCreditLedger


class FakeQuery:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        return self

    def limit(self, n):
        self.items = self.items[:n]
        return self

    def all(self):
        return list(self.items)

    def first(self):
        return self.items[0] if self.items else None

    def scalar(self):
        return self.items[0] if self.items else None

    def count(self):
        return len(self.items)


class FakeDb:
    def __init__(self, jobs=None, ledger=None):
        self.jobs = jobs or []
        self.ledger = ledger or []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "EmailOutbox":
            return FakeQuery(self.jobs)
        if name == "SmsCreditLedger":
            return FakeQuery(self.ledger)
        return FakeQuery([])

    def add(self, item):
        if isinstance(item, SmsCreditLedger):
            self.ledger.append(item)

    def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar=lambda: 0, first=lambda: None)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        return None


@pytest.fixture
def sms_job():
    return SimpleNamespace(
        id=101,
        user_id=1,
        customer_id=1,
        rule_id=10,
        run_id=None,
        channel="sms",
        status="queued",
        next_attempt_at=None,
        attempt_count=0,
        last_error=None,
        lock_owner=None,
        lock_acquired_at=None,
        provider_message_id=None,
        to_email="+44123456789",
        body="Hello",
        subject="SMS",
        template="chasing_first",
        payload_json={
            "eligibility_kind": "chasing",
            "customer_id": 1,
            "sequence_id": 1,
            "step_id": 1,
            "rule_id": 10,
            "channel": "sms",
            "generated_at_utc": "2026-01-01T00:00:00Z",
            "supersession_key": "customer:1:channel:sms:sequence:1",
        },
        delivery_status="queued",
        updated_at=None,
        provider="postmark",
    )


def _wire_common(monkeypatch, db, job):
    monkeypatch.setattr(outbox_worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(outbox_worker, "_requeue_stale_processing", lambda db, stale_seconds=120: 0)
    monkeypatch.setattr(outbox_worker, "_claim_one_due_job", lambda db: job if job.status == "queued" else None)
    monkeypatch.setattr(outbox_worker, "_preflight_sms_settings_or_fail", lambda db, j: True)
    monkeypatch.setattr(outbox_worker, "_ensure_sms_pricing", lambda db: SimpleNamespace(sms_send_cost=5, credit_send_pause_threshold=100))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 1000)
    monkeypatch.setattr(outbox_worker, "estimate_sms_segments", lambda body: SimpleNamespace(segments=1))
    monkeypatch.setattr(outbox_worker, "_coerce_payload", lambda p: p if isinstance(p, dict) else {})


def test_revalidation_failure_cancels_no_send_no_debit(monkeypatch, sms_job):
    db = FakeDb(jobs=[sms_job])
    _wire_common(monkeypatch, db, sms_job)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, row: SimpleNamespace(valid_to_send=False, reason="no_longer_overdue"))
    twilio_calls = {"n": 0}
    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", lambda db, j: twilio_calls.__setitem__("n", twilio_calls["n"] + 1))

    outbox_worker.process_once()
    assert sms_job.status == "canceled"
    assert sms_job.last_error == "no_longer_overdue"
    assert twilio_calls["n"] == 0
    assert len(db.ledger) == 0


def test_threshold_insufficient_cancels(monkeypatch, sms_job):
    db = FakeDb(jobs=[sms_job])
    _wire_common(monkeypatch, db, sms_job)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, row: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 50)
    monkeypatch.setattr(outbox_worker, "_cancel_sms_siblings", lambda *args, **kwargs: 0)

    outbox_worker.process_once()
    assert sms_job.status == "canceled"
    assert sms_job.last_error == "insufficient_credits"
    assert len(db.ledger) == 0


def test_exact_cost_insufficient_cancels(monkeypatch, sms_job):
    db = FakeDb(jobs=[sms_job])
    _wire_common(monkeypatch, db, sms_job)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, row: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 101)
    monkeypatch.setattr(outbox_worker, "estimate_sms_segments", lambda body: SimpleNamespace(segments=30))
    monkeypatch.setattr(outbox_worker, "_cancel_sms_siblings", lambda *args, **kwargs: 0)

    outbox_worker.process_once()
    assert sms_job.status == "canceled"
    assert sms_job.last_error == "insufficient_credits"
    assert len(db.ledger) == 0


def test_funded_sms_creates_debit_and_calls_twilio(monkeypatch, sms_job):
    db = FakeDb(jobs=[sms_job])
    _wire_common(monkeypatch, db, sms_job)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, row: SimpleNamespace(valid_to_send=True, reason="valid"))
    calls = {"n": 0}

    def _tw(db, j):
        calls["n"] += 1
        return "SM123"

    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", _tw)

    outbox_worker.process_once()
    assert calls["n"] == 1
    debits = [x for x in db.ledger if x.entry_type == "debit" and x.reason == "sms_send"]
    assert len(debits) == 1


def test_twilio_failure_creates_reversal(monkeypatch, sms_job):
    db = FakeDb(jobs=[sms_job])
    _wire_common(monkeypatch, db, sms_job)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, row: SimpleNamespace(valid_to_send=True, reason="valid"))

    def _tw(db, j):
        raise RuntimeError("boom")

    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", _tw)

    outbox_worker.process_once()
    reversals = [x for x in db.ledger if x.entry_type == "credit" and x.reason == "sms_send_reversal"]
    assert len(reversals) == 1


def test_idempotency_no_duplicate_reversal_same_attempt(monkeypatch, sms_job):
    existing_rev = SmsCreditLedger(
        user_id=1,
        entry_type="credit",
        amount=5,
        reason="sms_send_reversal",
        reference_id="sms:outbox:101:attempt:1:reversal",
        details={},
    )
    db = FakeDb(jobs=[sms_job], ledger=[existing_rev])
    _wire_common(monkeypatch, db, sms_job)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, row: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", lambda db, j: (_ for _ in ()).throw(RuntimeError("boom")))

    outbox_worker.process_once()
    revs = [x for x in db.ledger if x.reason == "sms_send_reversal"]
    assert len(revs) == 1
