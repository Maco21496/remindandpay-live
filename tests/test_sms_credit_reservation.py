from api.app.services.sms_credit_reservation import (
    build_sms_outbox_debit_reference,
    build_sms_outbox_reversal_reference,
)


def test_sms_outbox_reference_helpers():
    assert build_sms_outbox_debit_reference(123) == "sms:outbox:123"
    assert build_sms_outbox_reversal_reference(123) == "sms:outbox:123:reversal"
