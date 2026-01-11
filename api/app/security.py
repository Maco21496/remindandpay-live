# api/app/security.py
import os
from passlib.context import CryptContext

# Match the stored hashes: bcrypt $2b$
pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__ident="2b",  # ensure $2b$ prefix
    # bcrypt__rounds=12,  # defaults to 12; uncomment to force
)

def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain or "")

def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return pwd_ctx.verify(plain or "", hashed)
    except Exception:
        return False

def get_secret_key() -> str:
    return os.getenv("APP_SECRET", "dev-change-me")
