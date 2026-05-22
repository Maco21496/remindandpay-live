from types import SimpleNamespace

from api.app.routers import sms_webhooks


class _FakeQuery:
    def __init__(self, items):
        self.items = items

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.items[0] if self.items else None


class _FakeDb:
    def __init__(self, existing=None, reserved=None):
        self.existing = existing
        self.reserved = reserved
        self.added = []

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "SmsCreditLedger":
            if self.existing is not None:
                return _FakeQuery([self.existing])
            if self.reserved is not None:
                return _FakeQuery([self.reserved])
            return _FakeQuery([])
        return _FakeQuery([])

    def add(self, item):
        self.added.append(item)

    def commit(self):
        return None


def test_reservation_guard_updates_reserved_no_messagesid_debit(monkeypatch):
    reserved = SimpleNamespace(details={}, reason="sms_send", entry_type="debit")
    outbox = SimpleNamespace(id=42, user_id=1, to_email="+44", customer_id=7)
    db = _FakeDb(existing=None, reserved=reserved)

    monkeypatch.setattr(sms_webhooks, "_lookup_outbox_by_sid", lambda db, sid: outbox)
    monkeypatch.setattr(sms_webhooks, "_twilio_fetch_message_details", lambda a, b: {"num_segments": 1})

    sms_webhooks._record_sms_debit(
        db,
        settings=SimpleNamespace(user_id=1),
        params={"MessageSid": "SMA", "MessageStatus": "sent", "AccountSid": "ACx", "To": "+44"},
    )

    assert reserved.details.get("message_sid") == "SMA"
    assert not any(getattr(x, "reference_id", None) == "SMA" for x in db.added)


def test_legacy_messagesid_path_still_creates_debit(monkeypatch):
    outbox = SimpleNamespace(id=42, user_id=1, to_email="+44", customer_id=7)
    db = _FakeDb(existing=None, reserved=None)
    monkeypatch.setattr(sms_webhooks, "_lookup_outbox_by_sid", lambda db, sid: outbox)
    monkeypatch.setattr(sms_webhooks, "_ensure_pricing", lambda db: SimpleNamespace(sms_send_cost=5))
    monkeypatch.setattr(sms_webhooks, "_twilio_fetch_message_details", lambda a, b: {"num_segments": 2})

    sms_webhooks._record_sms_debit(
        db,
        settings=SimpleNamespace(user_id=1),
        params={"MessageSid": "SMLEG", "MessageStatus": "sent", "AccountSid": "ACx", "To": "+44"},
    )

    created = [x for x in db.added if getattr(x, "reference_id", None) == "SMLEG"]
    assert created
