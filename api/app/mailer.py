# FINAL VERSION OF app/mailer.py
import os
import requests
from typing import Optional, Tuple, Dict, Any, List
from html import escape
from base64 import b64encode
from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext
import logging

from .services.statement_pdf import render_statement_pdf_html, render_pdf_from_html
from .crypto_secrets import decrypt_secret

log = logging.getLogger("mailer")

POSTMARK_SEND_URL = "https://api.postmarkapp.com/email"

# Platform defaults (env-overridable)
PLATFORM_FROM_NAME  = os.getenv("PLATFORM_FROM_NAME", "Remind & Pay")
PLATFORM_FROM_EMAIL = os.getenv("PLATFORM_FROM_EMAIL", "accounts@remindandpay.com")

class MailResult:
    def __init__(self, ok: bool, message_id: Optional[str] = None,
                 error: Optional[str] = None, code: Optional[int] = None,
                 permanent: bool = False):
        self.ok = ok
        self.message_id = message_id
        self.error = error
        self.code = code
        self.permanent = permanent
    def __repr__(self) -> str:
        return f"MailResult(ok={self.ok}, id={self.message_id!r}, code={self.code!r}, permanent={self.permanent}, error={self.error!r})"

def send_via_postmark(
    server_token: str,
    From: str,
    To: str,
    Subject: str,
    HtmlBody: str,
    TextBody: str = "",
    attachments: Optional[List[Dict[str, str]]] = None,
) -> MailResult:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": server_token,
    }
    payload = {
        "From": From,
        "To": To,
        "Subject": Subject,
        "HtmlBody": HtmlBody,
        "TextBody": TextBody or " ",
        "MessageStream": "outbound",
    }
    if attachments:
        payload["Attachments"] = attachments
    try:
        r = requests.post(POSTMARK_SEND_URL, headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return MailResult(True, message_id=str(data.get("MessageID")))
        code = None
        permanent = False
        msg_text = r.text
        try:
            jd = r.json()
            code = int(jd.get("ErrorCode")) if "ErrorCode" in jd else None
            msg_text = jd.get("Message") or msg_text
            if code in (412, 300, 405, 406):
                permanent = True
            if 400 <= r.status_code < 500 and r.status_code != 429:
                permanent = True
        except Exception:
            if 400 <= r.status_code < 500 and r.status_code != 429:
                permanent = True
        return MailResult(False, error=f"{r.status_code}: {msg_text}", code=code, permanent=permanent)
    except Exception as e:
        return MailResult(False, error=str(e), code=None, permanent=False)

# ---------------------------
# Statement email composition
# ---------------------------

def _render_statement_pdf_from_html(html: str) -> Optional[bytes]:
    b = render_pdf_from_html(html)
    if b:
        log.info("Rendered PDF via wkhtmltopdf")
    return b

def _render_statement_pdf_html(
    db: Session,
    user_id: int,
    customer_id: int,
    date_to: Optional[str] = None,
    include_after_payments: bool = False,
) -> Optional[str]:
    return render_statement_pdf_html(db, user_id, customer_id, date_to, include_after_payments)

def compose_statement_html_text(
    message: str,
    customer_name: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    statement_url: Optional[str],
) -> Tuple[str, str]:
    safe_msg_html = escape(message or "").replace("\n", "<br>")
    date_html = ""
    if date_from or date_to:
        df = date_from or "–"
        dt = date_to or "–"
        date_html = f"<p><strong>Period:</strong> {df} – {dt}</p>"
    link_html = ""
    if statement_url:
        link_html = f'<p><a href="{statement_url}" target="_blank">View your statement</a></p>'
    html_parts = [
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;',
        'font-size:14px;color:#111;">',
        f"<p>{safe_msg_html}</p>",
        date_html,
        link_html,
        f'<p style="color:#666;margin-top:16px;">Sent for <strong>{escape(customer_name or "Customer")}</strong>.</p>',
        "</div>",
    ]
    html = "".join(html_parts)
    text_lines = [message or ""]
    if date_from or date_to:
        text_lines.append("")
        text_lines.append(f"Period: {date_from or '-'} – {date_to or '-'}")
    if statement_url:
        text_lines.append("")
        text_lines.append(f"View your statement: {statement_url}")
    text = "\n".join(text_lines)
    return html, text

def _get_user_server_token(db: Session, user_id: int) -> str:
    """
    Returns the user's Postmark Server token.
    REQUIRED: postmark_server_token_enc must be present and decryptable.
    """
    row = db.execute(sqltext("""
        SELECT postmark_server_token_enc
          FROM account_email_settings
         WHERE user_id = :uid
         LIMIT 1
    """), {"uid": user_id}).first()
    if not row or not getattr(row, "postmark_server_token_enc", None):
        raise RuntimeError("No encrypted Postmark server token configured for this account")
    try:
        return decrypt_secret(row.postmark_server_token_enc)
    except Exception as e:
        raise RuntimeError(f"Failed to decrypt Postmark server token: {e}")

def _resolve_sender_and_token(db: Session, user_id: int) -> Tuple[str, str]:
    """
    Always uses the user's Postmark Server token (encrypted).
    - platform: From = PLATFORM_FROM_NAME/PLATFORM_FROM_EMAIL
    - custom_domain: From = default_from_name/default_from_email
    """
    row = db.execute(sqltext("""
        SELECT mode, default_from_name, default_from_email
          FROM account_email_settings
         WHERE user_id = :uid
         LIMIT 1
    """), {"uid": user_id}).first()
    if not row:
        raise RuntimeError("Email settings not configured for this account")

    token = _get_user_server_token(db, user_id)

    if row.mode == "platform":
        from_addr = f"{PLATFORM_FROM_NAME} <{PLATFORM_FROM_EMAIL}>"
    else:
        from_addr = f"{row.default_from_name} <{row.default_from_email}>"

    return from_addr, token

def send_statement_for_user(
    db: Session,
    user_id: int,
    to_email: str,
    subject: str,
    message: str,
    payload_json: Optional[Dict[str, Any]],
    customer_name: Optional[str],
    attach_pdf: bool = True,
) -> MailResult:
    try:
        from_addr, server_token = _resolve_sender_and_token(db, user_id)
    except Exception as e:
        return MailResult(False, error=str(e))

    p = payload_json or {}
    html, text = compose_statement_html_text(
        message=message,
        customer_name=customer_name or "Customer",
        date_from=p.get("date_from"),
        date_to=p.get("date_to"),
        statement_url=p.get("statement_url"),
    )

    attachments = None
    if attach_pdf:
        statement_html: Optional[str] = p.get("statement_html")
        if not statement_html and p.get("customer_id"):
            statement_html = _render_statement_pdf_html(
                db=db,
                user_id=user_id,
                customer_id=int(p.get("customer_id")),
                date_to=p.get("date_to"),
            )
        if not statement_html and p.get("statement_url"):
            try:
                resp = requests.get(p["statement_url"], timeout=10)
                if resp.ok and resp.text:
                    statement_html = resp.text
            except Exception:
                pass
        if statement_html:
            pdf_bytes = _render_statement_pdf_from_html(statement_html)
            if pdf_bytes:
                filename = p.get("pdf_filename") or f"Statement-{(customer_name or 'Customer').strip().replace(' ', '_')}.pdf"
                attachments = [{
                    "Name": filename,
                    "Content": b64encode(pdf_bytes).decode("ascii"),
                    "ContentType": "application/pdf",
                }]

    return send_via_postmark(
        server_token=server_token,
        From=from_addr,
        To=to_email,
        Subject=subject,
        HtmlBody=html,
        TextBody=text,
        attachments=attachments,
    )

def _html_to_text_fallback(html: str) -> str:
    import re
    s = html or ""
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*p\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*div\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*li\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s or " "

def send_chasing_for_user(
    db: Session,
    user_id: int,
    to_email: str,
    subject: str,
    html_body: str,
) -> MailResult:
    try:
        from_addr, server_token = _resolve_sender_and_token(db, user_id)
    except Exception as e:
        return MailResult(False, error=str(e))
    text_body = _html_to_text_fallback(html_body or "")
    return send_via_postmark(
        server_token=server_token,
        From=from_addr,
        To=to_email,
        Subject=subject or "",
        HtmlBody=html_body or "",
        TextBody=text_body,
    )
