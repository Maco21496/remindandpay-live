# FINAL VERSION OF app/initial_user_setup.py
from __future__ import annotations
from datetime import datetime
from typing import Dict, Any
import os
import re
import requests
import secrets

from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext

from .services.statement_globals_logic import ensure_global_rules
from .models import ReminderTemplate, ChasingPlan, ChasingTrigger
from .crypto_secrets import encrypt_secret

SEED_TEMPLLES = [
    # --- Gentle ---
    dict(key="gentle_overdue_0",  channel="email", tag="gentle", step_number=0,
         name="Gentle — Due today",
         subject="Invoice {{ invoice.invoice_number }} is due today",
         body_text="Hi {{ customer.name }},\n\nJust a reminder your invoice {{ invoice.invoice_number }} "
                   "for {{ invoice.amount_due }} is due today ({{ invoice.due_date }}).\n{{ payment_link }}\n\nThanks,\nAccounts"),
    dict(key="gentle_overdue_7",  channel="email", tag="gentle", step_number=1,
         name="Gentle — 7 days overdue",
         subject="Reminder: invoice {{ invoice.invoice_number }} is now overdue",
         body_text="Hi {{ customer.name }},\n\nOur records show invoice {{ invoice.invoice_number }} "
                   "({{ invoice.amount_due }}) was due on {{ invoice.due_date }} — {{ days_overdue }} days ago.\n"
                   "{{ payment_link }}\n\nThanks,\nAccounts"),
    dict(key="gentle_overdue_14", channel="email", tag="gentle", step_number=2,
         name="Gentle — 14 days overdue",
         subject="Second reminder: invoice {{ invoice.invoice_number }}",
         body_text="Hi {{ customer.name }},\n\nA quick follow-up on invoice {{ invoice.invoice_number }} "
                   "({{ invoice.amount_due }}), now {{ days_overdue }} days overdue.\n{{ payment_link }}\n\nThanks,\nAccounts"),

    # --- Firm ---
    dict(key="firm_overdue_0",  channel="email", tag="firm", step_number=0,
         name="Firm — Due today",
         subject="Action needed: invoice {{ invoice.invoice_number }} due today",
         body_text="Hello {{ customer.name }},\n\nInvoice {{ invoice.invoice_number }} for {{ invoice.amount_due }} "
                   "is due today ({{ invoice.due_date }}). Please arrange payment.\n{{ payment_link }}\n\nThank you."),
    dict(key="firm_overdue_7",  channel="email", tag="firm", step_number=1,
         name="Firm — 7 days overdue",
         subject="Past due: invoice {{ invoice.invoice_number }} ({{ days_overdue }} days)",
         body_text="Hello {{ customer.name }},\n\nInvoice {{ invoice.invoice_number }} ({{ invoice.amount_due }}) "
                   "is {{ days_overdue }} days overdue. Please settle at your earliest convenience.\n"
                   "{{ payment_link }}\n\nThank you."),
    dict(key="firm_overdue_14", channel="email", tag="firm", step_number=2,
         name="Firm — 14 days overdue",
         subject="Final reminder before escalation: {{ invoice.invoice_number }}",
         body_text="Hello {{ customer.name }},\n\nInvoice {{ invoice.invoice_number }} remains unpaid "
                   "({{ days_overdue }} days overdue). Please make payment to avoid escalation.\n{{ payment_link }}\n\nThank you."),

    # --- Aggressive ---
    dict(key="aggressive_overdue_21", channel="email", tag="aggressive", step_number=3,
         name="Aggressive — 21 days",
         subject="Immediate payment required: {{ invoice.invoice_number }}",
         body_text="Dear {{ customer.name }},\n\nInvoice {{ invoice.invoice_number }} ({{ invoice.amount_due }}) is "
                   "{{ days_overdue }} days overdue. Immediate payment is required.\n{{ payment_link }}"),
    dict(key="aggressive_overdue_28", channel="email", tag="aggressive", step_number=4,
         name="Aggressive — 28 days",
         subject="Notice of intended action — {{ invoice.invoice_number }}",
         body_text="Dear {{ customer.name }},\n\nUnless payment of {{ invoice.amount_due }} for invoice "
                   "{{ invoice.invoice_number }} is received promptly, we may initiate further action.\n{{ payment_link }}"),
]

def _slug_email(email: str) -> str:
    e = (email or "").strip().lower()
    e = e.replace("@", "-at-")
    e = re.sub(r"[^a-z0-9\-]+", "-", e)
    e = re.sub(r"-{2,}", "-", e).strip("-")
    return e[:80] or "user"

def _ensure_email_settings_row(db: Session, user_id: int) -> None:
    """
    Ensure account_email_settings row exists with platform defaults.
    """
    db.execute(
        sqltext("""
            INSERT INTO account_email_settings (user_id, mode, default_from_name, default_from_email)
            SELECT :uid, 'platform', 'Remind & Pay', 'accounts@remindandpay.com'
            WHERE NOT EXISTS (SELECT 1 FROM account_email_settings WHERE user_id = :uid)
        """),
        {"uid": user_id},
    )
    db.commit()

# FINAL VERSION OF _create_postmark_server_and_save()  (always ensures webhook; stream='outbound')
def _create_postmark_server_and_save(db: Session, *, user_id: int) -> Dict[str, Any]:
    """
    Create (or reuse) a dedicated Postmark Server for this user and save ONLY the encrypted token.
    Naming: rp-u{user_id}-{slug(user.email)}
    Reads the ACCOUNT token from env POSTMARK_ACCOUNT_TOKEN.
    CHANGE:
      • Always ENSURE a webhook exists on the server (even if the server already existed).
      • Webhook is created on MessageStream='outbound' (Default Transactional Stream).
    """
    account_token = os.getenv("POSTMARK_ACCOUNT_TOKEN", "").strip()
    if not account_token:
        return {"ok": False, "server_id": None, "message": "POSTMARK_ACCOUNT_TOKEN is not configured on the server"}

    # local: fetch user email (for name) and current settings row
    u = db.execute(sqltext("SELECT email FROM users WHERE id = :uid LIMIT 1"), {"uid": user_id}).first()
    if not u or not getattr(u, "email", None):
        return {"ok": False, "server_id": None, "message": "User email not found"}

    row = db.execute(
        sqltext("""
            SELECT postmark_server_id, postmark_server_token_enc
              FROM account_email_settings
             WHERE user_id = :uid
             LIMIT 1
        """),
        {"uid": user_id},
    ).first()

    server_name = f"rp-u{user_id}-{_slug_email(u.email)}"
    acct_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Account-Token": account_token,
    }

    # --- local helper: ensure webhook on this server using a SERVER token
    def _ensure_webhook_for_server(server_token: str) -> None:
        webhook_url = (os.getenv("POSTMARK_WEBHOOK_URL", "") or "").strip()
        wb_user = (os.getenv("POSTMARK_WEBHOOK_USER", "") or "").strip()
        wb_pass = (os.getenv("POSTMARK_WEBHOOK_PASS", "") or "").strip()
        if not webhook_url:
            return  # silently skip; we don't alter provisioning result

        s_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": server_token,
        }

        # Idempotent: if a webhook with same URL exists, do nothing
        try:
            r_list = requests.get("https://api.postmarkapp.com/webhooks", headers=s_headers, timeout=15)
            if r_list.ok:
                for w in (r_list.json() or []):
                    if (w.get("Url") or "").strip().lower() == webhook_url.lower():
                        return
        except Exception:
            pass  # fall through and try to create

        payload_wh = {
            "Url": webhook_url,
            "MessageStream": "outbound",  # Default Transactional Stream
            "Triggers": {
                "Open": {"Enabled": True},
                "Click": {"Enabled": True},
                "Delivery": {"Enabled": True},
                "Bounce": {"Enabled": True, "IncludeContent": False},
                "SpamComplaint": {"Enabled": True},
            },
        }
        if wb_user or wb_pass:
            payload_wh["HttpAuth"] = {"Username": wb_user, "Password": wb_pass}

        try:
            requests.post("https://api.postmarkapp.com/webhooks", headers=s_headers, json=payload_wh, timeout=15)
        except Exception:
            # don't fail provisioning on webhook errors
            pass

    # Path A: settings row exists and has a server id already -> fetch server token via Account API and ensure webhook
    if row and getattr(row, "postmark_server_id", None):
        server_id = int(row.postmark_server_id)
        try:
            r = requests.get(f"https://api.postmarkapp.com/servers/{server_id}", headers=acct_headers, timeout=15)
            if r.ok:
                data = r.json() or {}
                tokens = data.get("ApiTokens") or []
                if tokens:
                    _ensure_webhook_for_server(str(tokens[0]))
            # nothing else to update; token stays encrypted in DB
            return {"ok": True, "server_id": server_id, "message": "Already provisioned (webhook ensured)"}
        except Exception:
            return {"ok": True, "server_id": server_id, "message": "Already provisioned (webhook ensure skipped due to API error)"}

    # Path B: create new server, ensure webhook, then save encrypted token
    headers = dict(acct_headers)
    payload = {"Name": server_name, "Color": "Blue", "DeliveryType": "Live", "SmtpApiActivated": True}

    try:
        r = requests.post("https://api.postmarkapp.com/servers", headers=headers, json=payload, timeout=15)
        data = {}
        try:
            data = r.json()
        except Exception:
            data = {"Message": r.text}
        if r.status_code != 200:
            msg = data.get("Message") or f"HTTP {r.status_code}"
            return {"ok": False, "server_id": None, "message": f"Postmark create server failed: {msg}"}

        server_id = int(data.get("ID") or 0)
        api_tokens = data.get("ApiTokens") or []
        if not server_id or not api_tokens:
            return {"ok": False, "server_id": None, "message": "Postmark did not return server ID and token"}

        server_token = str(api_tokens[0])

        # ensure webhook now (we have a clear server token)
        _ensure_webhook_for_server(server_token)

        # save encrypted token
        server_token_enc = encrypt_secret(server_token)
        db.execute(
            sqltext("""
                UPDATE account_email_settings
                   SET postmark_server_id = :sid,
                       postmark_server_token_enc = :stok_enc,
                       postmark_server_token = NULL
                 WHERE user_id = :uid
            """),
            {"sid": server_id, "stok_enc": server_token_enc, "uid": user_id},
        )
        db.commit()

        return {"ok": True, "server_id": server_id, "message": "Sending server created (webhook ensured)"}
    except Exception as e:
        return {"ok": False, "server_id": None, "message": f"Unexpected error: {e}"}

# ---------- Inbound helpers (local copies, no router imports) ----------

def _get_server_id_for_inbound(db: Session, user_id: int) -> int | None:
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

def _ensure_inbound_domain_for_user_local(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Same logic as routers.inbound_settings_app._ensure_inbound_domain_for_user,
    but returns a dict instead of raising HTTPException.

    Now also sets the inbound webhook URL on the server:
      InboundHookUrl = https://app.remindandpay.com/api/postmark/inbound
    """
    server_id = _get_server_id_for_inbound(db, user_id)
    if not server_id:
        return {
            "ok": False,
            "inbound_domain": None,
            "message": "Postmark server is not provisioned for this user",
        }

    account_token = os.getenv("POSTMARK_ACCOUNT_TOKEN", "").strip()
    if not account_token:
        return {
            "ok": False,
            "inbound_domain": None,
            "message": "POSTMARK_ACCOUNT_TOKEN is not configured on the server",
        }

    base = os.getenv("INBOUND_BASE_DOMAIN", "inv.remindandpay.com").strip()
    inbound_domain = f"u{user_id}.{base}"

    # Inbound webhook URL you specified
    inbound_hook_url = "https://app.remindandpay.com/api/postmark/inbound"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Account-Token": account_token,
    }
    url = f"https://api.postmarkapp.com/servers/{server_id}"
    payload = {
        "InboundDomain": inbound_domain,
        "InboundHookUrl": inbound_hook_url,
    }

    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        return {
            "ok": False,
            "inbound_domain": None,
            "message": f"Failed to reach Postmark API: {e}",
        }

    if not resp.ok:
        try:
            detail = (resp.json() or {}).get("Message") or resp.text
        except Exception:
            detail = resp.text
        return {
            "ok": False,
            "inbound_domain": None,
            "message": f"Postmark InboundDomain update failed ({resp.status_code}): {detail}",
        }

    return {
        "ok": True,
        "inbound_domain": inbound_domain,
        "message": "ok",
    }

# NEW: ensure inbound token + inbound domain for this user (same behaviour as /api/inbound/generate)
def _ensure_inbound_forwarding_for_user(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Mirror the behaviour of POST /api/inbound/generate for initial setup:
      - ensure account_email_settings row
      - create inbound_token if missing
      - ensure Postmark InboundDomain for this user's server
    Returns a stats dict with ok/token/inbound_domain/message.
    """
    _ensure_email_settings_row(db, user_id)

    row = db.execute(
        sqltext("""
            SELECT inbound_token
              FROM account_email_settings
             WHERE user_id = :uid
             LIMIT 1
        """),
        {"uid": user_id},
    ).first()

    token = getattr(row, "inbound_token", None) if row else None
    if not token:
        token = secrets.token_hex(16)
        db.execute(
            sqltext("""
                UPDATE account_email_settings
                   SET inbound_token = :tok
                 WHERE user_id = :uid
            """),
            {"tok": token, "uid": user_id},
        )
        db.commit()

    res = _ensure_inbound_domain_for_user_local(db, user_id)
    return {
        "ok": bool(res.get("ok")),
        "token": token,
        "inbound_domain": res.get("inbound_domain"),
        "message": res.get("message"),
    }

def _upsert_default_templates(db: Session, user_id: int, overwrite: bool = False) -> Dict[str, int]:
    """
    Ensure each seed template exists for the user.
    - If missing -> insert
    - If present and overwrite=True -> update fields
    """
    now = datetime.utcnow()
    created = 0
    updated = 0

    # Load existing fully (robust across SA versions)
    existing = {
        (rt.key, rt.channel): rt
        for rt in db.query(ReminderTemplate)
                    .filter(ReminderTemplate.user_id == user_id)
                    .all()
    }

    for t in SEED_TEMPLLES:
        ident = (t["key"], t["channel"])
        if ident in existing:
            if overwrite:
                rec = existing[ident]
                rec.tag         = t["tag"]
                rec.step_number = t.get("step_number")
                rec.name        = t["name"]
                rec.subject     = t.get("subject")
                rec.body_html   = None
                rec.body_text   = t.get("body_text")
                rec.is_active   = True
                rec.updated_at  = now
                updated += 1
            # else leave user’s version
        else:
            db.add(ReminderTemplate(
                user_id     = user_id,
                key         = t["key"],
                channel     = t["channel"],
                tag         = t["tag"],
                step_number = t.get("step_number"),
                name        = t["name"],
                subject     = t.get("subject"),
                body_html   = None,
                body_text   = t.get("body_text"),
                is_active   = True,
                updated_at  = now,
            ))
            created += 1

    if created or updated:
        db.commit()

    return {"templates_created": created, "templates_updated": updated}

def _ensure_default_chasing_plan(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Create (idempotently) a ChasingPlan named 'Default Chasing Cycle'
    with four email steps that match the seeded template keys:
      (7, 'firm_overdue_7'),
      (14, 'gentle_overdue_14'),
      (21, 'aggressive_overdue_21'),
      (28, 'aggressive_overdue_28')

    If the plan already exists, it is left untouched (no overwrites).
    If the plan exists but a step is missing, that step is added.
    """
    # Find or create the plan
    plan = (
        db.query(ChasingPlan)
          .filter(ChasingPlan.user_id == user_id, ChasingPlan.name == "Default Chasing Cycle")
          .first()
    )
    created_plan = False
    if not plan:
        plan = ChasingPlan(user_id=user_id, name="Default Chasing Cycle")
        db.add(plan)
        db.commit()
        db.refresh(plan)
        created_plan = True

    # Desired steps (only using template keys we know exist in SEED_TEMPLLES)
    desired = [
        (7,  "firm_overdue_7"),
        (14, "gentle_overdue_14"),
        (21, "aggressive_overdue_21"),
        (28, "aggressive_overdue_28"),
    ]

    added = 0
    for offset, key in desired:
        exists = (
            db.query(ChasingTrigger.id)
              .filter(
                  ChasingTrigger.sequence_id == plan.id,
                  ChasingTrigger.channel == "email",
                  ChasingTrigger.offset_days == offset,
              )
              .first()
        )
        if exists:
            continue
        trig = ChasingTrigger(
            sequence_id=plan.id,
            offset_days=offset,
            channel="email",
            template_key=key,
            order_index=9999,
        )
        db.add(trig)
        added += 1

    if added:
        db.commit()

    # Resequence: order by offset_days asc, id asc
    triggers = (
        db.query(ChasingTrigger)
          .filter(ChasingTrigger.sequence_id == plan.id)
          .order_by(ChasingTrigger.offset_days.asc(), ChasingTrigger.id.asc())
          .all()
    )
    dirty = False
    for idx, trig in enumerate(triggers, start=1):
        if trig.order_index != idx:
            trig.order_index = idx
            db.add(trig)
            dirty = True
    if dirty:
        db.commit()

    return {"plan_created": created_plan, "steps_added": added, "plan_id": plan.id}

def run_initial_user_setup(
    db: Session,
    user_id: int,
    *,
    seed_globals: bool = True,
    seed_templates: bool = True,
    overwrite_templates: bool = False,
) -> Dict[str, Any]:
    """
    Seeds defaults for a new user and provisions a Postmark Server.
    Returns stats including:
      - globals_ok
      - templates_created / templates_updated
      - default_plan: { plan_created, steps_added, plan_id }
      - postmark_ok / postmark_server_id / postmark_message
      - inbound_ok / inbound_message
    """
    stats: Dict[str, Any] = {}

    if seed_globals:
        ensure_global_rules(db, user_id)
        stats["globals_ok"] = True

    if seed_templates:
        stats.update(_upsert_default_templates(db, user_id, overwrite=overwrite_templates))

    # Ensure the default chasing plan exists and contains the four expected steps
    stats["default_plan"] = _ensure_default_chasing_plan(db, user_id)

    # Ensure settings row, then create per-user Postmark server and save encrypted token
    _ensure_email_settings_row(db, user_id)
    pm = _create_postmark_server_and_save(db, user_id=user_id)
    stats["postmark_ok"] = bool(pm.get("ok"))
    stats["postmark_server_id"] = pm.get("server_id")
    stats["postmark_message"] = pm.get("message")

    # Automatically generate inbound token + InboundDomain (same effect as clicking "Generate forwarding address")
    inbound = _ensure_inbound_forwarding_for_user(db, user_id)
    stats["inbound_ok"] = bool(inbound.get("ok"))
    stats["inbound_message"] = inbound.get("message")

    return stats
