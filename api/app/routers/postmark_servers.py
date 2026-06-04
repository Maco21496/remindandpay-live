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
from ..postmark_safety import (
    is_staging_environment,
    build_postmark_server_create_payload,
    resolve_postmark_webhook_url,
)

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

def _ensure_webhook_for_server(server_token: str) -> None:
    webhook_url = resolve_postmark_webhook_url()
    wb_user = (os.getenv("POSTMARK_WEBHOOK_USER", "") or "").strip()
    wb_pass = (os.getenv("POSTMARK_WEBHOOK_PASS", "") or "").strip()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": server_token,
    }
    try:
        existing = requests.get(f"{POSTMARK_API_BASE}/webhooks", headers=headers, timeout=15)
        if existing.ok:
            for webhook in (existing.json() or []):
                if (webhook.get("Url") or "").strip().lower() == webhook_url.lower():
                    return
    except Exception:
        pass

    payload = {
        "Url": webhook_url,
        "MessageStream": "outbound",
        "Triggers": {
            "Open": {"Enabled": True},
            "Click": {"Enabled": True},
            "Delivery": {"Enabled": True},
            "Bounce": {"Enabled": True, "IncludeContent": False},
            "SpamComplaint": {"Enabled": True},
        },
    }
    if wb_user or wb_pass:
        payload["HttpAuth"] = {"Username": wb_user, "Password": wb_pass}
    requests.post(f"{POSTMARK_API_BASE}/webhooks", headers=headers, json=payload, timeout=15)


def _create_server(account_token: str, name: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Account-Token": account_token,
    }
    try:
        payload = build_postmark_server_create_payload(name)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
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

    account_token = os.getenv("POSTMARK_ACCOUNT_TOKEN", "").strip()
    if not account_token:
        raise HTTPException(400, "POSTMARK_ACCOUNT_TOKEN is not configured on the server.")

    # If already provisioned (has id and encrypted token), return only after
    # staging verifies that the copied/existing Postmark server is Sandbox.
    if getattr(s, "postmark_server_id", None) and getattr(s, "postmark_server_token_enc", None):
        server_id = int(s.postmark_server_id)
        if is_staging_environment():
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Account-Token": account_token,
            }
            r = requests.get(f"{POSTMARK_API_BASE}/servers/{server_id}", headers=headers, timeout=15)
            if not r.ok:
                raise HTTPException(502, "Staging could not verify existing Postmark server DeliveryType; clear copied Postmark state and reprovision.")
            if ((r.json() or {}).get("DeliveryType") or "") != "Sandbox":
                raise HTTPException(400, "Staging refuses to reuse a non-Sandbox Postmark server; clear copied Postmark state and reprovision.")
        return {
            "ok": True,
            "created": False,
            "server_id": server_id,
            "server_token_saved": True,
        }

    server_name = f"rp-u{user.id}-{_slug_email(user.email)}"
    data = _create_server(account_token, server_name)
    server_id = int(data.get("ID") or 0)
    api_tokens = data.get("ApiTokens") or []
    if not server_id or not api_tokens:
        raise HTTPException(502, "Postmark did not return a server ID and token.")

    server_token = str(api_tokens[0])
    try:
        _ensure_webhook_for_server(server_token)
    except Exception as e:
        raise HTTPException(502, f"Create server succeeded but webhook setup failed: {e}")

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
