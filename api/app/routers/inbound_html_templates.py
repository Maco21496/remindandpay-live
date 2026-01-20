# NEW: HTML invoice templates
from __future__ import annotations
import json
import re
import secrets
from typing import Any, Dict, Optional

from fastapi import Depends, Form, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..shared import APIRouter
from ..database import get_db
from .auth import require_user

router = APIRouter(prefix="/api/inbound/html", tags=["html-invoice-imports"])


def _get_user_id(user_obj: Any) -> int:
    uid = getattr(user_obj, "id", None)
    if uid is None and isinstance(user_obj, dict):
        uid = user_obj.get("id")
    try:
        uid = int(uid) if uid is not None else None
    except Exception:
        uid = None
    if uid is None:
        raise HTTPException(status_code=401, detail="Current user id not available.")
    return uid


@router.get("/templates")
def list_templates(
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
):
    user_id = _get_user_id(current_user)
    rows = db.execute(
        text(
            """
            SELECT html_template_name, html_created_at, html_updated_at, html_subject_token
            FROM ic_html_template
            WHERE html_user_id = :uid
            ORDER BY html_updated_at DESC, html_created_at DESC
            """
        ),
        {"uid": user_id},
    ).fetchall()

    templates = []
    for row in rows:
        name, created_at, updated_at, subject_token = row
        if not name:
            continue
        templates.append(
            {
                "template_name": name,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "subject_token": subject_token,
            }
        )

    return {"ok": True, "templates": templates}


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower()
    return cleaned or "template"


def _generate_subject_token(name: str) -> str:
    return f"html-{_slugify(name)[:24]}-{secrets.token_hex(3)}"


def _ensure_subject_token(
    db: Session,
    user_id: int,
    template_name: str,
    existing: Optional[str],
) -> str:
    token = (existing or "").strip()
    if token:
        return token
    token = _generate_subject_token(template_name)
    db.execute(
        text(
            """
            UPDATE ic_html_template
            SET html_subject_token = :token
            WHERE html_user_id = :uid AND html_template_name = :name
            """
        ),
        {"uid": user_id, "name": template_name, "token": token},
    )
    db.commit()
    return token


@router.get("/load-template")
def load_template(
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
    template_name: Optional[str] = None,
):
    user_id = _get_user_id(current_user)
    params: Dict[str, Any] = {"uid": user_id}

    if template_name:
        cleaned = template_name.strip()
        params["name"] = cleaned
        row = db.execute(
            text(
                """
                SELECT html_template_name, html_template_json, html_body, html_email_body, html_subject_token
                FROM ic_html_template
                WHERE html_user_id = :uid AND html_template_name = :name
                ORDER BY html_updated_at DESC, html_created_at DESC
                LIMIT 1
                """
            ),
            params,
        ).fetchone()
    else:
        row = db.execute(
            text(
                """
                SELECT html_template_name, html_template_json, html_body, html_email_body, html_subject_token
                FROM ic_html_template
                WHERE html_user_id = :uid
                ORDER BY html_updated_at DESC, html_created_at DESC
                LIMIT 1
                """
            ),
            params,
        ).fetchone()

    template_name_out: Optional[str] = None
    template_json: Any = {}
    html_body: str = ""
    html_email_body: str = ""
    subject_token: Optional[str] = None

    if row:
        template_name_out, template_json, html_body, html_email_body, subject_token = row
        if isinstance(template_json, str):
            try:
                template_json = json.loads(template_json)
            except Exception:
                template_json = {}
        if template_name_out:
            subject_token = _ensure_subject_token(
                db,
                user_id,
                template_name_out,
                subject_token,
            )

    return {
        "ok": True,
        "template_name": template_name_out,
        "template_json": template_json or {},
        "html_body": html_body or "",
        "html_email_body": html_email_body or "",
        "subject_token": subject_token,
    }


@router.post("/save-template")
def save_template(
    template_json: str = Form(...),
    template_name: str = Form(""),
    html_body: str = Form(""),
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
):
    user_id = _get_user_id(current_user)
    cleaned_name = (template_name or "").strip()
    if not cleaned_name:
        raise HTTPException(status_code=400, detail="template_name is required.")

    try:
        parsed = json.loads(template_json or "{}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid template_json: {exc}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="template_json must be an object.")

    result = db.execute(
        text(
            """
            UPDATE ic_html_template
            SET html_template_json = CAST(:tpl AS JSON),
                html_body = :body,
                html_updated_at = NOW()
            WHERE html_user_id = :uid
              AND html_template_name = :name
            """
        ),
        {
            "uid": user_id,
            "name": cleaned_name,
            "tpl": json.dumps(parsed, ensure_ascii=False),
            "body": html_body or "",
        },
    )

    subject_token = None
    if result.rowcount == 0:
        subject_token = _generate_subject_token(cleaned_name)
        db.execute(
            text(
                """
                INSERT INTO ic_html_template
                    (html_user_id, html_template_name, html_template_json, html_body, html_email_body, html_subject_token, html_created_at, html_updated_at)
                VALUES
                    (:uid, :name, CAST(:tpl AS JSON), :body, :email_body, :token, NOW(), NOW())
                """
            ),
            {
                "uid": user_id,
                "name": cleaned_name,
                "tpl": json.dumps(parsed, ensure_ascii=False),
                "body": html_body or "",
                "email_body": "",
                "token": subject_token,
            },
        )

    if not subject_token:
        subject_token = _ensure_subject_token(
            db,
            user_id,
            cleaned_name,
            None,
        )

    db.commit()
    return {"ok": True, "subject_token": subject_token}


@router.get("/sample")
def load_sample(
    template_name: str,
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
):
    user_id = _get_user_id(current_user)
    row = db.execute(
        text(
            """
            SELECT html_subject_token
            FROM ic_html_template
            WHERE html_user_id = :uid AND html_template_name = :name
            LIMIT 1
            """
        ),
        {"uid": user_id, "name": template_name},
    ).fetchone()
    if not row or not row.html_subject_token:
        raise HTTPException(status_code=404, detail="template not found")

    subject_token = row.html_subject_token
    rows = db.execute(
        text(
            """
            SELECT payload_json, received_at
            FROM inbound_invoice_queue
            WHERE user_id = :uid
              AND source = 'email'
            ORDER BY id DESC
            LIMIT 50
            """
        ),
        {"uid": user_id},
    ).fetchall()

    def _subject_from_payload(payload: dict) -> str:
        subj = payload.get("Subject") or payload.get("OriginalSubject") or ""
        if subj:
            return subj
        headers = payload.get("Headers") or []
        if isinstance(headers, list):
            for header in headers:
                if not isinstance(header, dict):
                    continue
                if (header.get("Name") or "").lower() == "subject":
                    return header.get("Value") or ""
        return ""

    def _payload_contains_token(payload: dict, token: str) -> bool:
        if not token:
            return False
        subject = _subject_from_payload(payload)
        if token in (subject or ""):
            return True
        for key in ("HtmlBody", "TextBody", "StrippedTextReply", "StrippedHtmlReply"):
            value = payload.get(key)
            if isinstance(value, str) and token in value:
                return True
        try:
            return token in json.dumps(payload, ensure_ascii=False)
        except Exception:
            return False

    matched_payload = None
    matched_at = None
    for row in rows:
        payload = row.payload_json or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            continue
        if _payload_contains_token(payload, subject_token):
            matched_payload = payload
            matched_at = row.received_at
            break

    if not matched_payload:
        raise HTTPException(status_code=404, detail="no sample email found")

    html_body = matched_payload.get("HtmlBody") or ""
    if html_body:
        db.execute(
            text(
                """
                UPDATE ic_html_template
                SET html_email_body = :body,
                    html_updated_at = NOW()
                WHERE html_user_id = :uid AND html_template_name = :name
                """
            ),
            {"uid": user_id, "name": template_name, "body": html_body},
        )
        db.commit()
    return {
        "ok": True,
        "subject_token": subject_token,
        "html_body": html_body,
        "received_at": matched_at.isoformat() if matched_at else None,
    }
