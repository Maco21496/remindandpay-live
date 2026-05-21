from api.app.routers.outbox_scheduler import URLS


def test_scheduler_urls_exact_match():
    assert URLS == [
        "/api/statement_reminders/statements/enqueue-due",
        "/api/chasing_reminders/enqueue-due",
        "/api/credits/number-renewals/enqueue-due",
    ]


def test_scheduler_urls_absent_paths():
    assert "/api/sms/billing/enqueue-due" not in URLS
    assert "/api/billing/stripe/topup-reconcile/enqueue-due" not in URLS
