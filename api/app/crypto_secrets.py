# FINAL VERSION OF app/crypto_secrets.py
import os
import base64
from secrets import token_bytes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _get_key() -> bytes:
    k = os.getenv("APP_SECRETS_KEY")
    if not k:
        raise RuntimeError("APP_SECRETS_KEY env var is required (32 bytes, base64/hex/raw).")
    # Accept base64, hex, or raw 16/24/32-byte strings
    try:
        # hex
        if all(c in "0123456789abcdefABCDEF" for c in k) and len(k) in (32, 48, 64):
            b = bytes.fromhex(k)
            if len(b) in (16, 24, 32): return b
    except Exception:
        pass
    try:
        # base64
        b = base64.b64decode(k, validate=True)
        if len(b) in (16, 24, 32): return b
    except Exception:
        pass
    b = k.encode("utf-8")
    if len(b) in (16, 24, 32):
        return b
    raise RuntimeError("APP_SECRETS_KEY must decode to 16/24/32 bytes (AES-128/192/256).")

def encrypt_secret(plaintext: str) -> str:
    key = _get_key()
    aes = AESGCM(key)
    nonce = token_bytes(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")

def decrypt_secret(token_b64: str) -> str:
    key = _get_key()
    raw = base64.b64decode(token_b64)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    pt = aes.decrypt(nonce, ct, None)
    return pt.decode("utf-8")
