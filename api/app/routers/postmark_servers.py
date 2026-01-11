# FINAL VERSION OF app/routers/postmark_servers.py
from __future__ import annotations
import os
import re
import requests
from typing import Dict, Any

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext

from ..shared import APIRouter
from ..database import get_db
from .auth import require_user
from ..crypto_secrets import encrypt_secret

router = APIRouter(prefix="/api/postmark/servers", tags=["postmark"])

POSTMARK_API_BASE = "https://api.postmarkapp.com"

def _slug_email(email: str) -> str:
    e = (email or "").strip().lower()
    e = e.replace("@", "-at-")
    e = re.sub(r"[^a-z0-9\-]+", "-", e)
    e = re.sub(r"-{2,}", "-", e).strip("-")
    return e[:80] or "user"

def _ensure_settings_row(db: Session, user_id: int) -> None:
    db.execute(
        sqltext("""
            INSERT INTO account_email_settings (user_id, mode, default_from_name, default_from_email)
            SELECT :uid, 'platform', 'Remind & Pay', 'accounts@remindandpay.com'
            WHERE NOT EXISTS (SELECT 1 FROM account_email_settings WHERE user_id = :uid)
        """),
        {"uid": user_id},
    )
    db.commit()

def _load_settings(db: Session, user_id: int):
    return db.execute(
        sqltext("""
            SELECT aes.user_id,
                   aes.postmark_server_id,
                   aes.postmark_server_token_enc
              FROM account_email_settings aes
             WHERE aes.user_id = :uid
             LIMIT 1
        """),
        {"uid": user_id},
    ).first()

def _create_server(account_token: str, name: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Account-Token": account_token,
    }
    payload = {
        "Name": name,
        "Color": "Blue",
        "DeliveryType": "Live",
        "SmtpApiActivated": True,
    }
    r = requests.post(f"{POSTMARK_API_BASE}/servers", headers=headers, json=payload, timeout=15)
    try:
        data = r.json()
    except Exception:
        data = {"Message": r.text}

    if r.status_code != 200:
        msg = data.get("Message") or "Postmark /servers failed"
        raise HTTPException(status_code=502, detail=f"Create server failed: {msg}")

    return data  # includes ID, ApiTokens

@router.post("/init")
def init_user_server(
    user = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Idempotently create a dedicated Postmark Server for the logged-in user.
    - Reads the ACCOUNT token from env: POSTMARK_ACCOUNT_TOKEN.
    - Stores ONLY the encrypted server token (postmark_server_token_enc) and server id.
    - Plaintext column is set to NULL.
    """
    _ensure_settings_row(db, user.id)
    s = _load_settings(db, user.id)
    if not s:
        raise HTTPException(500, "Email settings row missing after ensure.")

    # If already provisioned (has id and encrypted token), return
    if getattr(s, "postmark_server_id", None) and getattr(s, "postmark_server_token_enc", None):
        return {
            "ok": True,
            "created": False,
            "server_id": int(s.postmark_server_id),
            "server_token_saved": True,
        }

    account_token = os.getenv("POSTMARK_ACCOUNT_TOKEN", "").strip()
    if not account_token:
        raise HTTPException(400, "POSTMARK_ACCOUNT_TOKEN is not configured on the server.")

    server_name = f"rp-u{user.id}-{_slug_email(user.email)}"
    data = _create_server(account_token, server_name)
    server_id = int(data.get("ID") or 0)
    api_tokens = data.get("ApiTokens") or []
    if not server_id or not api_tokens:
        raise HTTPException(502, "Postmark did not return a server ID and token.")

    server_token = str(api_tokens[0])
    try:
        server_token_enc = encrypt_secret(server_token)
    except Exception as e:
        raise HTTPException(500, f"Encrypt server token failed: {e}")

    db.execute(
        sqltext("""
            UPDATE account_email_settings
               SET postmark_server_id = :sid,
                   postmark_server_token_enc = :stok_enc,
                   postmark_server_token = NULL
             WHERE user_id = :uid
        """),
        {"sid": server_id, "stok_enc": server_token_enc, "uid": user.id},
    )
    db.commit()

    return {
        "ok": True,
        "created": True,
        "server_id": server_id,
        "server_token_saved": True,
    }
