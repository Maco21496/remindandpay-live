# FINAL VERSION OF api/app/routers/inbound_settings_app.py  (adds strict mapping validation + active block template)
from __future__ import annotations
import os, json, secrets, requests
from typing import Optional, Dict, Any
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy import text as sqltext
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from .auth import require_user

router = APIRouter(prefix="/api/inbound", tags=["inbound"])

# ---------- Schemas ----------

class InboundSettingsOut(BaseModel):
    inbound_address: Optional[str] = None   # e.g. inb_<token>@u<id>.inv.remindandpay.com
    inbound_token: Optional[str] = None
    inbound_active: bool
    inbound_reader: Optional[str] = Field(None, regex="^(pdf|html)$")
    inbound_mapping_json: Optional[Dict[str, Any]] = None
    inbound_block_template_name: Optional[str] = None
    inbound_last_seen_at: Optional[str] = None

class InboundSaveIn(BaseModel):
    inbound_active: bool
    inbound_reader: str = Field(..., regex="^(pdf|html)$")
    inbound_mapping_json: Dict[str, Any]
    inbound_block_template_name: Optional[str] = None

    @validator("inbound_mapping_json")
    def _validate_mapping(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """
        Strict validator for PDF reader mappings so we save only well-formed configs.
        Required shape for PDF:
          {
            "reader": "pdf",
            "version": 1,
            "date_format": "dd/MM/yyyy",
            "customer_match": {
              "mode": "name" | "email" | "external_id" | "none",
              "create_if_missing": bool,
              "name_normalize": {...},
              "external_id_field": "customer_code"   # only if mode=external_id
            },
            "fields": {
              "invoice_number": {...regex...},
              "issue_date": {...regex...},
              "amount_due": {...regex...},
              ... optional keys (due_date, customer_name, customer_email, customer_code, currency_hint)
            }
          }
        """
        if not isinstance(v, dict):
            raise ValueError("mapping must be an object")

        reader = v.get("reader")
        if reader != "pdf":
            # allow saving non-pdf later, but enforce minimal shape when reader is pdf
            return v

        # required top-level
        for key in ("version", "date_format", "customer_match", "fields"):
            if key not in v:
                raise ValueError(f"mapping.{key} is required for PDF reader")

        # required fields regex
        fields = v.get("fields") or {}
        for req in ("invoice_number", "issue_date", "amount_due"):
            node = fields.get(req)
            if not isinstance(node, dict) or not node.get("regex"):
                raise ValueError(f"fields.{req}.regex is required")

        # customer_match
        cm = v.get("customer_match") or {}
        mode = cm.get("mode")
        if mode not in ("name", "email", "external_id", "none"):
            raise ValueError("customer_match.mode must be one of name|email|external_id|none")
        if mode == "external_id" and not cm.get("external_id_field"):
            raise ValueError("customer_match.external_id_field is required when mode=external_id")

        # booleans
        cim = cm.get("create_if_missing")
        if not isinstance(cim, bool):
            raise ValueError("customer_match.create_if_missing must be true/false")

        return v

    @validator("inbound_block_template_name", always=True)
    def _require_template_if_pdf_and_active(cls, v, values):
        """
        When inbound reader is 'pdf' AND inbound is active, require an active
        block template name so the webhook knows exactly which template to use.
        """
        reader = values.get("inbound_reader")
        active = values.get("inbound_active")
        if reader in ("pdf", "html") and active and not (v or "").strip():
            raise ValueError("inbound_block_template_name is required when inbound_reader is pdf/html and inbound_active is true")
        return v

# ---------- Helpers ----------

def _get_row(db: Session, user_id: int):
    row = db.execute(
        sqltext("""
            SELECT user_id, inbound_token, inbound_active, inbound_reader,
                   inbound_mapping_json, inbound_block_template_name, inbound_last_seen_at
              FROM account_email_settings
             WHERE user_id = :uid
             LIMIT 1
        """),
        {"uid": user_id},
    ).first()
    if not row:
        db.execute(
            sqltext("""
                INSERT INTO account_email_settings (user_id, mode, default_from_name, default_from_email)
                VALUES (:uid, 'platform', 'Remind & Pay', 'accounts@remindandpay.com')
                ON DUPLICATE KEY UPDATE user_id = user_id
            """),
            {"uid": user_id},
        )
        db.commit()
        row = db.execute(
            sqltext("""
                SELECT user_id, inbound_token, inbound_active, inbound_reader,
                       inbound_mapping_json, inbound_block_template_name, inbound_last_seen_at
                  FROM account_email_settings
                 WHERE user_id = :uid
                 LIMIT 1
            """),
            {"uid": user_id},
        ).first()
    return row

def _get_server_id(db: Session, user_id: int) -> Optional[int]:
    r = db.execute(
        sqltext("""
            SELECT postmark_server_id
              FROM account_email_settings
             WHERE user_id = :uid
             LIMIT 1
        """),
        {"uid": user_id},
    ).first()
    try:
        return int(r.postmark_server_id) if r and r.postmark_server_id else None
    except Exception:
        return None

def _build_address(token: Optional[str], user_id: int) -> Optional[str]:
    if not token:
        return None
    base = os.getenv("INBOUND_BASE_DOMAIN", "inv.remindandpay.com").strip()
    sub = f"u{user_id}.{base}"
    local = f"inb_{token}"
    return f"{local}@{sub}"

def _ensure_inbound_domain_for_user(db: Session, user_id: int) -> str:
    server_id = _get_server_id(db, user_id)
    if not server_id:
        raise HTTPException(400, "Postmark server is not provisioned for this user")

    account_token = os.getenv("POSTMARK_ACCOUNT_TOKEN", "").strip()
    if not account_token:
        raise HTTPException(500, "POSTMARK_ACCOUNT_TOKEN is not configured on the server")

    base = os.getenv("INBOUND_BASE_DOMAIN", "inv.remindandpay.com").strip()
    inbound_domain = f"u{user_id}.{base}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Account-Token": account_token,
    }
    url = f"https://api.postmarkapp.com/servers/{server_id}"
    payload = {"InboundDomain": inbound_domain}

    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        raise HTTPException(502, f"Failed to reach Postmark API: {e}")

    if not resp.ok:
        try:
            detail = (resp.json() or {}).get("Message") or resp.text
        except Exception:
            detail = resp.text
        raise HTTPException(502, f"Postmark InboundDomain update failed ({resp.status_code}): {detail}")

    return inbound_domain

# ---------- Routes ----------

@router.get("/settings", response_model=InboundSettingsOut)
def get_settings(db: Session = Depends(get_db), user = Depends(require_user)):
    row = _get_row(db, user.id)
    mapping = None
    if row.inbound_mapping_json:
        try:
            mapping = json.loads(row.inbound_mapping_json)
        except Exception:
            mapping = None
    return InboundSettingsOut(
        inbound_address=_build_address(row.inbound_token, user.id),
        inbound_token=row.inbound_token,
        inbound_active=bool(row.inbound_active),
        inbound_reader=row.inbound_reader,
        inbound_mapping_json=mapping,
        inbound_block_template_name=row.inbound_block_template_name,
        inbound_last_seen_at=(row.inbound_last_seen_at.isoformat(sep=" ") if row.inbound_last_seen_at else None),
    )

@router.post("/generate", response_model=InboundSettingsOut)
def generate_address(db: Session = Depends(get_db), user = Depends(require_user)):
    row = _get_row(db, user.id)
    token = row.inbound_token or secrets.token_hex(16)
    if not row.inbound_token:
        db.execute(
            sqltext("""
                UPDATE account_email_settings
                   SET inbound_token = :tok
                 WHERE user_id = :uid
            """),
            {"tok": token, "uid": user.id},
        )
        db.commit()

    _ensure_inbound_domain_for_user(db, user.id)
    return get_settings(db, user)

@router.post("/save", response_model=InboundSettingsOut)
def save_settings(body: InboundSaveIn, db: Session = Depends(get_db), user = Depends(require_user)):
    # Validate mapping shape via Pydantic (already done in InboundSaveIn)
    # And ensure token exists if enabling
    row = _get_row(db, user.id)
    if body.inbound_active and not row.inbound_token:
        raise HTTPException(400, "Generate your inbound address first")

    db.execute(
        sqltext("""
            UPDATE account_email_settings
               SET inbound_active = :act,
                   inbound_reader = :rd,
                   inbound_mapping_json = :mp,
                   inbound_block_template_name = :tpl
             WHERE user_id = :uid
        """),
        {
            "act": 1 if body.inbound_active else 0,
            "rd": body.inbound_reader,
            "mp": json.dumps(body.inbound_mapping_json, ensure_ascii=False),
            "tpl": (body.inbound_block_template_name or "").strip() or None,
            "uid": user.id,
        },
    )
    db.commit()
    return get_settings(db, user)
