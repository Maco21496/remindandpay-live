from types import SimpleNamespace

import pytest

from api.app.routers import outbox_worker


class FakeQuery:
    def __init__(self, items):
        self.items = list(items)
        self._filtered = list(items)

    def filter(self, *args, **kwargs):
        filtered = self._filtered
        for expr in args:
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            field = getattr(left, "name", None)
            value = getattr(right, "value", None)
            if field and value is not None:
                filtered = [x for x in filtered if getattr(x, field, None) == value]
        self._filtered = filtered
        return self

    def first(self):
        return self._filtered[0] if self._filtered else None

    def all(self):
        return list(self._filtered)


class FakeDb:
    def __init__(self, jobs=None, ledger=None):
        self.jobs = jobs or []
        self.ledger = ledger or []

    def query(self, model):
        n = getattr(model, "__name__", "")
        if n == "SmsCreditLedger":
            return FakeQuery(self.ledger)
        if n == "EmailOutbox":
            return FakeQuery(self.jobs)
        return FakeQuery([])

    def add(self, item):
        if getattr(item, "reference_id", None) is not None and getattr(item, "reason", "") in {"sms_send", "sms_send_reversal"}:
            self.ledger.append(item)

    def flush(self):
        return None

    def commit(self):
        return None


def _job():
    return SimpleNamespace(
        id=101,
        user_id=1,
        customer_id=1,
        rule_id=10,
        run_id=None,
        channel="sms",
        status="queued",
        attempt_count=0,
        last_error=None,
        lock_owner="worker",
        lock_acquired_at=1,
        provider_message_id=None,
        to_email="+44123456789",
        body="Hello",
        payload_json={"eligibility_kind": "chasing", "channel": "sms"},
    )


def test_revalidation_failure_cancels_no_send_no_debit(monkeypatch):
    job = _job()
    db = FakeDb(jobs=[job])
    monkeypatch.setattr(outbox_worker, "_preflight_sms_settings_or_fail", lambda db, j: True)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, j: SimpleNamespace(valid_to_send=False, reason="no_longer_overdue"))
    calls = {"tw": 0}
    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", lambda db, j: calls.__setitem__("tw", calls["tw"] + 1))

    outbox_worker._process_sms_job(db, job)
    assert job.status == "canceled"
    assert job.last_error == "no_longer_overdue"
    assert calls["tw"] == 0
    assert len(db.ledger) == 0


def test_threshold_insufficient_cancels(monkeypatch):
    job = _job()
    db = FakeDb(jobs=[job])
    monkeypatch.setattr(outbox_worker, "_preflight_sms_settings_or_fail", lambda db, j: True)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, j: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_ensure_sms_pricing", lambda db: SimpleNamespace(sms_send_cost=5, credit_send_pause_threshold=100))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 50)

    outbox_worker._process_sms_job(db, job)
    assert job.status == "canceled"
    assert job.last_error == "insufficient_credits"
    assert len(db.ledger) == 0


def test_exact_cost_insufficient_cancels(monkeypatch):
    job = _job()
    db = FakeDb(jobs=[job])
    monkeypatch.setattr(outbox_worker, "_preflight_sms_settings_or_fail", lambda db, j: True)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, j: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_ensure_sms_pricing", lambda db: SimpleNamespace(sms_send_cost=10, credit_send_pause_threshold=100))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 105)
    monkeypatch.setattr(outbox_worker, "estimate_sms_segments", lambda body: SimpleNamespace(segments=11))

    outbox_worker._process_sms_job(db, job)
    assert job.status == "canceled"
    assert job.last_error == "insufficient_credits"
    assert len(db.ledger) == 0


def test_funded_creates_debit_then_twilio(monkeypatch):
    job = _job()
    db = FakeDb(jobs=[job])
    monkeypatch.setattr(outbox_worker, "_preflight_sms_settings_or_fail", lambda db, j: True)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, j: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_ensure_sms_pricing", lambda db: SimpleNamespace(sms_send_cost=5, credit_send_pause_threshold=100))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 1000)
    monkeypatch.setattr(outbox_worker, "estimate_sms_segments", lambda body: SimpleNamespace(segments=2))
    calls = {"tw": 0}
    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", lambda db, j: calls.__setitem__("tw", calls["tw"] + 1) or "SM123")

    outbox_worker._process_sms_job(db, job)
    debits = [x for x in db.ledger if x.reason == "sms_send" and x.entry_type == "debit"]
    assert len(debits) == 1
    assert calls["tw"] == 1


def test_twilio_failure_creates_one_reversal(monkeypatch):
    job = _job()
    db = FakeDb(jobs=[job])
    monkeypatch.setattr(outbox_worker, "_preflight_sms_settings_or_fail", lambda db, j: True)
    monkeypatch.setattr(outbox_worker, "revalidate_chasing_sms_outbox", lambda db, j: SimpleNamespace(valid_to_send=True, reason="valid"))
    monkeypatch.setattr(outbox_worker, "_ensure_sms_pricing", lambda db: SimpleNamespace(sms_send_cost=5, credit_send_pause_threshold=100))
    monkeypatch.setattr(outbox_worker, "_effective_credit_balance_for_user", lambda db, user_id: 1000)
    monkeypatch.setattr(outbox_worker, "estimate_sms_segments", lambda body: SimpleNamespace(segments=1))
    monkeypatch.setattr(outbox_worker, "_send_sms_via_twilio", lambda db, j: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        outbox_worker._process_sms_job(db, job)

    reversals = [x for x in db.ledger if x.reason == "sms_send_reversal" and x.entry_type == "credit"]
    assert len(reversals) == 1


def test_sibling_cancel_scope_only_same_user_and_scope(monkeypatch):
    job = _job()
    sibling = _job()
    sibling.id = 102
    sibling.rule_id = 10
    other_user = _job()
    other_user.id = 103
    other_user.user_id = 2
    email_row = _job()
    email_row.id = 104
    email_row.channel = "email"
    db = FakeDb(jobs=[sibling, other_user, email_row])

    outbox_worker._cancel_sms_siblings(db, job, "insufficient_credits", {})
    assert sibling.status == "canceled"
    assert other_user.status == "queued"
    assert email_row.status == "queued"


def test_process_once_does_not_mark_canceled_sms_as_sent(monkeypatch):
    job = _job()
    job.id = 999
    db = FakeDb(jobs=[job], ledger=[])

    monkeypatch.setattr(outbox_worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(outbox_worker, "_requeue_stale_processing", lambda *args, **kwargs: 0)
    monkeypatch.setattr(outbox_worker, "_claim_one_due_job", lambda _db: job if job.status == "queued" else None)
    monkeypatch.setattr(outbox_worker, "_process_sms_job", lambda _db, j: outbox_worker._cancel_sms_row(_db, j, "missing_context"))

    monkeypatch.setattr(outbox_worker, "BATCH_SIZE", 1)
    monkeypatch.setattr(outbox_worker, "User", SimpleNamespace(id=SimpleNamespace(__eq__=lambda self, x: None)))
    monkeypatch.setattr(outbox_worker, "Customer", SimpleNamespace(id=SimpleNamespace(__eq__=lambda self, x: None)))

    class UserQuery:
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return SimpleNamespace(id=job.user_id)
        def count(self):
            return 1
    class GenericQuery(UserQuery):
        def scalar(self):
            return None
    def fake_query(model):
        name = getattr(model, "__name__", "")
        if name in {"User", "Customer"}:
            return UserQuery()
        if name == "EmailOutbox":
            return GenericQuery()
        return FakeQuery([])
    db.query = fake_query
    db.execute = lambda *args, **kwargs: SimpleNamespace(scalar=lambda: 1)
    db.rollback = lambda: None
    db.close = lambda: None

    sent = outbox_worker.process_once()
    assert sent == 0
    assert job.status == "canceled"
