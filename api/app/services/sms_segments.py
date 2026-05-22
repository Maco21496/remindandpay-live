from dataclasses import dataclass


GSM_7_BASIC_CHARS = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ "
    "!\"#¤%&'()*+,-./"
    "0123456789:;<=>?"
    "¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "ÄÖÑÜ§¿"
    "abcdefghijklmnopqrstuvwxyz"
    "äöñüà"
)

GSM_7_EXTENSION_CHARS = set("^{}\\[~]|€")


@dataclass(frozen=True)
class SmsSegmentEstimate:
    encoding: str
    length_units: int
    segments: int


def _normalize_line_endings(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def estimate_sms_segments(body: str) -> SmsSegmentEstimate:
    text = _normalize_line_endings(body)
    if text == "":
        return SmsSegmentEstimate(encoding="gsm7", length_units=0, segments=1)

    septets = 0
    is_gsm7 = True

    for ch in text:
        if ch in GSM_7_BASIC_CHARS:
            septets += 1
        elif ch in GSM_7_EXTENSION_CHARS:
            septets += 2
        else:
            is_gsm7 = False
            break

    if is_gsm7:
        single = 160
        multi = 153
        segments = 1 if septets <= single else ((septets + multi - 1) // multi)
        return SmsSegmentEstimate(encoding="gsm7", length_units=septets, segments=segments)

    length = len(text)
    single = 70
    multi = 67
    segments = 1 if length <= single else ((length + multi - 1) // multi)
    return SmsSegmentEstimate(encoding="ucs2", length_units=length, segments=segments)
