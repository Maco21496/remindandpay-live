# FINAL VERSION OF api/app/routers/email_settings.py
from typing import Optional
from html import escape

from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from fastapi import Depends

from ..shared import APIRouter, HTTPException, Session
from ..database import get_db
from ..models import Customer
from .auth import require_user  # scope by logged-in user
from ..mailer import send_via_postmark, send_statement_for_user
from ..crypto_secrets import decrypt_secret  # 👈 NEW: decrypt encrypted server token

router = APIRouter(prefix="/api/email", tags=["email"])

# ----------------- Pydantic models -----------------

class TestIn(BaseModel):
    to_email: EmailStr

class EmailSettingsOut(BaseModel):
    mode: str
    default_from_name: str
    default_from_email: EmailStr

class EmailSettingsIn(BaseModel):
    # mode: "platform" or "custom_domain"
    mode: str
    default_from_name: str
    default_from_email: EmailStr
    postmark_server_token: Optional[str] = None  # write-only (never returned)
    postmark_account_token: Optional[str] = None # write-only (never returned)

class StatementSendIn(BaseModel):
    customer_id: int
    to_email: EmailStr
    subject: str
    message: str
    date_from: Optional[str] = None    # 'YYYY-MM-DD'
    date_to: Optional[str] = None
    statement_url: Optional[str] = None     # optional; useful if public
    statement_html: Optional[str] = None    # preferred for PDF render
    pdf_filename: Optional[str] = None
    attach_pdf: Optional[bool] = True       # opt-out if needed

# ----------------- helpers -----------------

def _ensure_email_settings(db: Session, user_id: int) -> None:
    """Ensure a row exists for this user_id."""
    db.execute(
        text("""
            INSERT INTO account_email_settings (user_id, mode, default_from_name, default_from_email)
            SELECT :uid, 'platform', 'Remind & Pay', 'accounts@remindandpay.com'
            WHERE NOT EXISTS (SELECT 1 FROM account_email_settings WHERE user_id=:uid)
        """),
        {"uid": user_id},
    )
    db.commit()

def _get_email_settings(db: Session, user_id: int):
    # include encrypted column; plaintext is intentionally NOT used by /test anymore
    row = db.execute(
        text("""
            SELECT user_id,
                   mode,
                   default_from_name,
                   default_from_email,
                   postmark_server_token_enc
              FROM account_email_settings
             WHERE user_id = :uid
             LIMIT 1
        """),
        {"uid": user_id},
    ).first()
    return row

# ----------------- settings endpoints -----------------

@router.get("/settings", response_model=EmailSettingsOut)
def get_settings(user = Depends(require_user), db: Session = Depends(get_db)):
    _ensure_email_settings(db, user.id)
    s = _get_email_settings(db, user.id)
    if not s:
        raise HTTPException(404, "Email settings row missing for this user")
    return {
        "mode": s.mode,
        "default_from_name": s.default_from_name,
        "default_from_email": s.default_from_email,
    }

@router.post("/settings", response_model=EmailSettingsOut)
def update_settings(payload: EmailSettingsIn, user = Depends(require_user), db: Session = Depends(get_db)):
    if payload.mode not in ("platform", "custom_domain"):
        raise HTTPException(400, "Invalid mode")

    _ensure_email_settings(db, user.id)

    # Update visible fields
    db.execute(
        text("""
            UPDATE account_email_settings
               SET mode = :mode,
                   default_from_name = :name,
                   default_from_email = :email
             WHERE user_id = :uid
        """),
        {
            "mode": payload.mode,
            "name": payload.default_from_name,
            "email": payload.default_from_email,
            "uid": user.id,
        }
    )

    # Update tokens only if provided (legacy paths; /test no longer reads plaintext)
    if payload.postmark_server_token is not None:
        db.execute(
            text("""UPDATE account_email_settings
                       SET postmark_server_token = :srv
                     WHERE user_id = :uid
            """),
            {"srv": payload.postmark_server_token, "uid": user.id}
        )
    if payload.postmark_account_token is not None:
        db.execute(
            text("""UPDATE account_email_settings
                       SET postmark_account_token = :acc
                     WHERE user_id = :uid
            """),
            {"acc": payload.postmark_account_token, "uid": user.id}
        )

    db.commit()
    # Return only public view
    return {
        "mode": payload.mode,
        "default_from_name": payload.default_from_name,
        "default_from_email": payload.default_from_email,
    }

# ----------------- testing endpoint -----------------

@router.post("/test")
def send_test(body: TestIn, user = Depends(require_user), db: Session = Depends(get_db)):
    s = _get_email_settings(db, user.id)
    if not s:
        raise HTTPException(400, "Email settings row is missing for this user")
    # REQUIRE encrypted token only (no plaintext fallback)
    enc = getattr(s, "postmark_server_token_enc", None)
    if not enc:
        raise HTTPException(400, "Encrypted Postmark server token not set for this user")
    try:
        server_token = decrypt_secret(enc)
    except Exception as e:
        raise HTTPException(500, f"Could not decrypt Postmark server token: {e}")

    from_addr = f"{(s.default_from_name or 'Remind & Pay')} <{s.default_from_email}>"
    subject   = "Remind & Pay — test email"
    html      = "<p>This is a test email from Remind & Pay. 🎉</p>"

    res = send_via_postmark(
        server_token=server_token,
        From=from_addr,
        To=str(body.to_email),
        Subject=subject,
        HtmlBody=html,
        TextBody="This is a test email from Remind & Pay."
    )
    if not res.ok:
        raise HTTPException(502, f"Send failed: {res.error}")
    return {"ok": True, "message_id": res.message_id}

# ----------------- statement send endpoint -----------------

@router.post("/send-statement")
def send_statement_email(p: StatementSendIn, user = Depends(require_user), db: Session = Depends(get_db)):
    cust = db.get(Customer, p.customer_id)
    if not cust or getattr(cust, "user_id", None) != user.id:
        raise HTTPException(404, "Customer not found")  # strict ownership, no leaks

    payload = {
        "date_from": p.date_from,
        "date_to": p.date_to,
        "statement_url": p.statement_url,
        "statement_html": p.statement_html,   # if provided, we render PDF from this
        "pdf_filename": p.pdf_filename,
    }

    res = send_statement_for_user(
        db=db,
        user_id=user.id,
        to_email=str(p.to_email),
        subject=p.subject,
        message=p.message,
        payload_json=payload,
        customer_name=cust.name,
        attach_pdf=bool(p.attach_pdf) if p.attach_pdf is not None else True,
    )

    if not res.ok:
        raise HTTPException(status_code=502, detail=f"Postmark send failed: {res.error}")
    return {"ok": True, "message_id": res.message_id}
