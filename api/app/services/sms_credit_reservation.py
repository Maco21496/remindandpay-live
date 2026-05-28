SMS_OUTBOX_REFERENCE_PREFIX = "sms:outbox"
SMS_REVERSAL_SUFFIX = "reversal"


def build_sms_outbox_debit_reference(outbox_id: int) -> str:
    return f"{SMS_OUTBOX_REFERENCE_PREFIX}:{int(outbox_id)}"


def build_sms_outbox_reversal_reference(outbox_id: int) -> str:
    return f"{SMS_OUTBOX_REFERENCE_PREFIX}:{int(outbox_id)}:{SMS_REVERSAL_SUFFIX}"


def build_sms_reservation_metadata(*, outbox_id: int, estimated_segments: int, encoding: str) -> dict:
    return {
        "outbox_id": int(outbox_id),
        "estimated_segments": int(estimated_segments),
        "encoding": encoding,
    }
