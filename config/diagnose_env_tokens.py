# FINAL VERSION OF diagnose_env_tokens.py
import os
import base64
import binascii


def redacted(v: str, keep: int = 4) -> str:
    if not v:
        return "(unset)"
    v = v.strip()
    if len(v) <= keep * 2:
        return "*" * len(v)
    return v[:keep] + "..." + v[-keep:]


pmt = os.getenv("POSTMARK_SERVER_TOKEN_DEFAULT")
key = os.getenv("APP_SECRETS_KEY")

print("POSTMARK_SERVER_TOKEN_DEFAULT:", redacted(pmt))
print("APP_SECRETS_KEY:", redacted(key))

ok = False
note = None

if key:
    s = key.strip()
    try:
        raw = base64.b64decode(s, validate=True)
        ok = (len(raw) == 32)
        if not ok:
            note = f"decoded length = {len(raw)} (expected 32)"
    except (binascii.Error, ValueError) as e:
        note = f"decode failed: {e!s}"
else:
    note = "APP_SECRETS_KEY not set"

print("APP_SECRETS_KEY valid 32 bytes:", ok)
if note:
    print("Note:", note)
