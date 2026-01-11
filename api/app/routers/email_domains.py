# FINAL VERSION OF api/app/routers/email_domains.py
from __future__ import annotations

import os
from typing import Optional, List, Dict, Any

import requests
from pydantic import BaseModel, Field
from fastapi import Depends

from sqlalchemy import text

from ..shared import APIRouter, HTTPException, Session
from ..database import get_db
from .auth import require_user

router = APIRouter(prefix="/api/email/domains", tags=["email-domains"])

# Use the env var we actually set on the server
POSTMARK_ACCOUNT_TOKEN = (os.getenv("POSTMARK_ACCOUNT_TOKEN_DEFAULT", "") or "").strip()
PM_BASE = "https://api.postmarkapp.com"


# ---------- Pydantic Schemas ----------

class DomainStartIn(BaseModel):
    # Bare domain the customer owns (no scheme, no mailbox)
    domain: str = Field(..., min_length=3, max_length=255)


class DomainOut(BaseModel):
    id: int
    domain: str
    status: str                         # pending | verified
    dkim_verified: bool
    rp_verified: bool
    # DNS records customer needs to add (when pending)
    dkim1_host: Optional[str] = None
    dkim1_target: Optional[str] = None
    dkim2_host: Optional[str] = None
    dkim2_target: Optional[str] = None
    return_path_host: Optional[str] = None
    return_path_target: Optional[str] = None
    return_path_sub: Optional[str] = None
    # UX helpers
    can_use_for_sending: bool = False
    message: Optional[str] = None


class DomainListOut(BaseModel):
    items: List[DomainOut]


class VerifyOut(BaseModel):
    ok: bool
    dkim_verified: bool
    rp_verified: bool
    status: str                         # pending | verified


class UseOut(BaseModel):
    ok: bool


# ---------- Helpers ----------

def _pm_headers() -> Dict[str, str]:
    if not POSTMARK_ACCOUNT_TOKEN:
        raise HTTPException(500, "POSTMARK_ACCOUNT_TOKEN_DEFAULT is not configured on the server")
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Account-Token": POSTMARK_ACCOUNT_TOKEN,
    }


def _pm(method: str, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = PM_BASE + path
    r = requests.request(method.upper(), url, headers=_pm_headers(), json=json, timeout=15)
    try:
        data = r.json()
    except Exception:
        data = {}
    if not r.ok:
        msg = data.get("Message") or f"Postmark error {r.status_code}"
        raise HTTPException(502, f"{msg}")
    return data


def _row_to_out(db_row) -> DomainOut:
    out = DomainOut(
        id=int(db_row.id),
        domain=db_row.domain,
        status=db_row.status,
        dkim_verified=bool(db_row.dkim_verified),
        rp_verified=bool(db_row.rp_verified),
        dkim1_host=db_row.dkim1_host,
        dkim1_target=db_row.dkim1_target,
        dkim2_host=db_row.dkim2_host,
        dkim2_target=db_row.dkim2_target,
        return_path_host=db_row.return_path_host,
        return_path_target=db_row.return_path_target,
        return_path_sub=db_row.return_path_sub,
        can_use_for_sending=bool(db_row.dkim_verified) and bool(db_row.rp_verified),
        message=None,
    )
    if out.can_use_for_sending:
        out.message = "You already have a verified domain. Use it for sending?"
    return out


def _upsert_email_domain_row(db: Session, user_id: int, domain: str, pm_details: Dict[str, Any]) -> int:
    """
    Persist/refresh the DNS instruction fields from Postmark's domain details.
    Returns the row id in email_domains.

    CHANGE: For verified domains Postmark leaves *pending* DKIM fields empty and
    returns DKIMHost/DKIMTextValue instead. We now fall back to those so DKIM
    shows in the UI even when already verified.
    """
    # Prefer pending DKIM fields (when the user still needs to add them);
    # fall back to the verified DKIM fields when pending fields are empty.
    dkim1_host = (
        pm_details.get("DKIMPendingHost")
        or pm_details.get("DKIMHost")
        or None
    )
    dkim1_val = (
        pm_details.get("DKIMPendingTextValue")
        or pm_details.get("DKIMTextValue")
        or None
    )

    # Optional second DKIM record (only present in some responses)
    dkim2_host = pm_details.get("DKIMPendingHost2") or None
    dkim2_val  = pm_details.get("DKIMPendingTextValue2") or None

    # Return-Path CNAME details
    rp_sub    = pm_details.get("ReturnPathDomain") or None
    rp_host   = pm_details.get("ReturnPathDomain") or None
    rp_target = pm_details.get("ReturnPathDomainCNAMEValue") or None

    # Verification status flags
    dkim_verified = bool(pm_details.get("DKIMVerified"))
    rp_verified   = bool(pm_details.get("ReturnPathDomainVerified"))
    status        = "verified" if (dkim_verified and rp_verified) else "pending"

    # Upsert
    row = db.execute(
        text("""
            SELECT id FROM email_domains
             WHERE user_id = :uid AND domain = :dom
             LIMIT 1
        """),
        {"uid": user_id, "dom": domain},
    ).first()

    if row:
        db.execute(
            text("""
                UPDATE email_domains
                   SET return_path_sub   = :rpsub,
                       dkim1_host        = :dk1h, dkim1_target = :dk1v,
                       dkim2_host        = :dk2h, dkim2_target = :dk2v,
                       return_path_host  = :rph,  return_path_target = :rpt,
                       dkim_verified     = :dkok,
                       rp_verified       = :rpok,
                       status            = :st
                 WHERE id = :id
            """),
            {
                "rpsub": rp_sub,
                "dk1h": dkim1_host, "dk1v": dkim1_val,
                "dk2h": dkim2_host, "dk2v": dkim2_val,
                "rph": rp_host, "rpt": rp_target,
                "dkok": 1 if dkim_verified else 0,
                "rpok": 1 if rp_verified else 0,
                "st": status,
                "id": row.id,
            },
        )
        db.commit()
        return int(row.id)

    res = db.execute(
        text("""
            INSERT INTO email_domains
                (user_id, domain, return_path_sub,
                 dkim1_host, dkim1_target, dkim2_host, dkim2_target,
                 return_path_host, return_path_target,
                 dkim_verified, rp_verified, status)
            VALUES
                (:uid, :dom, :rpsub,
                 :dk1h, :dk1v, :dk2h, :dk2v,
                 :rph, :rpt,
                 :dkok, :rpok, :st)
        """),
        {
            "uid": user_id,
            "dom": domain,
            "rpsub": rp_sub,
            "dk1h": dkim1_host, "dk1v": dkim1_val,
            "dk2h": dkim2_host, "dk2v": dkim2_val,
            "rph": rp_host, "rpt": rp_target,
            "dkok": 1 if dkim_verified else 0,
            "rpok": 1 if rp_verified else 0,
            "st": status,
        },
    )
    db.commit()
    return int(res.lastrowid)


def _find_user_domain(db: Session, user_id: int):
    return db.execute(
        text("""SELECT * FROM email_domains WHERE user_id = :uid LIMIT 1"""),
        {"uid": user_id},
    ).first()


# ---------- Routes ----------

@router.get("", response_model=DomainListOut)
def list_domains(db: Session = Depends(get_db), user = Depends(require_user)):
    rows = db.execute(
        text("""
            SELECT id, domain, status, dkim_verified, rp_verified,
                   dkim1_host, dkim1_target, dkim2_host, dkim2_target,
                   return_path_host, return_path_target, return_path_sub
              FROM email_domains
             WHERE user_id = :uid
             ORDER BY id DESC
        """),
        {"uid": user.id},
    ).fetchall()

    return DomainListOut(items=[_row_to_out(r) for r in rows])


@router.post("/start", response_model=DomainOut)
def start_domain(body: DomainStartIn, db: Session = Depends(get_db), user = Depends(require_user)):
    """
    Step 1 of the wizard.
    If the user already has a domain row, return it immediately (skip entry).
    Otherwise, create/find the domain in Postmark, store instructions, and return.
    """
    # If user already has a domain, short-circuit into its status view.
    existing = _find_user_domain(db, user.id)
    if existing:
        out = _row_to_out(existing)
        out.message = out.message or "You already have a domain on file."
        return out

    dom = body.domain.strip().lower()
    if dom.startswith("http://") or dom.startswith("https://"):
        raise HTTPException(400, "Please enter a bare domain like example.com")

    # Exclusivity: if any other user already claimed this domain, block it
    other = db.execute(
        text("SELECT id FROM email_domains WHERE domain = :dom AND user_id <> :uid LIMIT 1"),
        {"dom": dom, "uid": user.id},
    ).first()
    if other:
        raise HTTPException(409, "This domain is already claimed by another account")

    # Try to find existing domain in Postmark first to avoid duplicate create errors
    pm_list = _pm("GET", "/domains?count=50&offset=0")
    match = next((d for d in pm_list.get("Domains", []) if (d.get("Name") or "").lower() == dom), None)

    if match:
        pm_id = int(match.get("ID"))
        pm_detail = _pm("GET", f"/domains/{pm_id}")
    else:
        # Choose a default Return-Path subdomain (Postmark returns the CNAME target)
        return_path_sub = f"pm-bounces.{dom}"
        pm_create = _pm("POST", "/domains", json={"Name": dom, "ReturnPathDomain": return_path_sub})
        pm_id = int(pm_create.get("ID"))
        pm_detail = _pm("GET", f"/domains/{pm_id}")

    row_id = _upsert_email_domain_row(db, user.id, dom, pm_detail)

    # Return the fresh row
    row = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": row_id, "uid": user.id},
    ).first()
    if not row:
        raise HTTPException(500, "Domain row not found after create")

    return _row_to_out(row)


@router.get("/{domain_id}", response_model=DomainOut)
def get_domain(domain_id: int, db: Session = Depends(get_db), user = Depends(require_user)):
    row = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": domain_id, "uid": user.id},
    ).first()
    if not row:
        raise HTTPException(404, "Domain not found")
    return _row_to_out(row)


@router.post("/{domain_id}/verify", response_model=VerifyOut)
def verify_domain(domain_id: int, db: Session = Depends(get_db), user = Depends(require_user)):
    """
    Step 2 of the wizard.
    Calls Postmark's 'Verify DKIM' and 'Verify Return-Path' endpoints.
    Refreshes our row with the latest verification state.
    """
    row = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": domain_id, "uid": user.id},
    ).first()
    if not row:
        raise HTTPException(404, "Domain not found")

    # Find the Postmark domain id by name
    pm_list = _pm("GET", "/domains?count=50&offset=0")
    pm_dom = next((d for d in pm_list.get("Domains", []) if (d.get("Name") or "").lower() == row.domain.lower()), None)
    if not pm_dom:
        raise HTTPException(404, "Domain is not present in Postmark account anymore")

    pm_id = int(pm_dom.get("ID"))

    # Verify DKIM and Return-Path
    _pm("PUT", f"/domains/{pm_id}/verifyDKIM")
    _pm("PUT", f"/domains/{pm_id}/verifyReturnPath")

    # Refresh detail and our row
    pm_detail = _pm("GET", f"/domains/{pm_id}")
    _upsert_email_domain_row(db, user.id, row.domain, pm_detail)

    # Reload
    row2 = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": domain_id, "uid": user.id},
    ).first()

    dkim_ok = bool(row2.dkim_verified)
    rp_ok   = bool(row2.rp_verified)
    status  = "verified" if (dkim_ok and rp_ok) else "pending"

    return VerifyOut(ok=True, dkim_verified=dkim_ok, rp_verified=rp_ok, status=status)


@router.post("/{domain_id}/use", response_model=UseOut)
def use_domain_for_sending(domain_id: int, db: Session = Depends(get_db), user = Depends(require_user)):
    """
    Switch account email settings to use this verified domain.
    Sets mode='custom_domain' and default_from_email = 'accounts@<domain>'.
    """
    row = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": domain_id, "uid": user.id},
    ).first()
    if not row:
        raise HTTPException(404, "Domain not found")
    if not (bool(row.dkim_verified) and bool(row.rp_verified)):
        raise HTTPException(400, "Domain is not verified yet")

    # Ensure account_email_settings row exists
    db.execute(
        text("""
            INSERT INTO account_email_settings (user_id, mode, default_from_name, default_from_email)
            SELECT :uid, 'platform', 'Remind & Pay', 'accounts@remindandpay.com'
            WHERE NOT EXISTS (SELECT 1 FROM account_email_settings WHERE user_id=:uid)
        """),
        {"uid": user.id},
    )

    # Flip to custom domain and set the from email
    db.execute(
        text("""
            UPDATE account_email_settings
               SET mode = 'custom_domain',
                   default_from_email = :from_email
             WHERE user_id = :uid
        """),
        {"from_email": f"accounts@{row.domain}", "uid": user.id},
    )
    db.commit()
    return UseOut(ok=True)

# FINAL VERSION OF get_domain_postmark_detail()
@router.get("/{domain_id}/debug-pm-detail")
def get_domain_postmark_detail(domain_id: int, db: Session = Depends(get_db), user = Depends(require_user)):
    """
    DEBUG: Fetch and return the raw Postmark domain detail JSON for this domain.
    Also upserts our email_domains row with whatever fields Postmark returns so
    the UI can reflect them on next open.
    """
    # Load our domain row
    row = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": domain_id, "uid": user.id},
    ).first()
    if not row:
        raise HTTPException(404, "Domain not found")

    # Find Postmark domain by name
    pm_list = _pm("GET", "/domains?count=50&offset=0")
    pm_dom = next((d for d in pm_list.get("Domains", []) if d.get("Name") == row.domain), None)
    if not pm_dom:
        raise HTTPException(404, "Domain is not present in Postmark account anymore")

    pm_id = int(pm_dom.get("ID"))

    # Get full detail JSON from Postmark
    pm_detail = _pm("GET", f"/domains/{pm_id}")

    # Upsert our row from this payload (so UI can re-open and see the latest)
    _upsert_email_domain_row(db, user.id, row.domain, pm_detail)

    # Return raw payload so we can see exactly which DKIM keys are present
    return {"pm_detail": pm_detail}

# FINAL VERSION OF DELETE ENDPOINT IN api/app/routers/email_domains.py
@router.delete("/{domain_id}")
def delete_domain(domain_id: int, db: Session = Depends(get_db), user = Depends(require_user)):
    """
    Remove the current user's domain setup:
      - If the domain exists in Postmark -> delete it there.
      - Always delete the local email_domains row.
    """
    row = db.execute(
        text("SELECT * FROM email_domains WHERE id=:id AND user_id=:uid LIMIT 1"),
        {"id": domain_id, "uid": user.id},
    ).first()
    if not row:
        raise HTTPException(404, "Domain not found")

    # Try to remove from Postmark (best-effort)
    try:
        pm_list = _pm("GET", "/domains?count=50&offset=0")
        pm_dom = next((d for d in pm_list.get("Domains", []) if (d.get("Name") or "").lower() == row.domain.lower()), None)
        if pm_dom:
            pm_id = int(pm_dom.get("ID"))
            _pm("DELETE", f"/domains/{pm_id}")
    except Exception:
        # We don't abort deletion if Postmark delete fails; we still remove our row.
        pass

    db.execute(text("DELETE FROM email_domains WHERE id=:id AND user_id=:uid"), {"id": domain_id, "uid": user.id})
    db.commit()
    return {"ok": True}

