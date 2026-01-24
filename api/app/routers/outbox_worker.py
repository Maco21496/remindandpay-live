# FINAL VERSION OF app/routers/outbox_worker.py
import json
import time
import os
import traceback
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text
import requests

from ..database import SessionLocal
from ..models import (
    AccountSmsSettings,
    EmailOutbox,
    ReminderEvent,
    Invoice,
    Customer,
    User,
    StatementRun,
)
from ..mailer import send_statement_for_user, send_chasing_for_user

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [outbox_worker] %(levelname)s: %(message)s"
)
log = logging.getLogger("outbox_worker")

# ---------- defaults (can be overridden by env at runtime) ----------
MAX_ATTEMPTS = 8
BATCH_SIZE   = 100
WORKER_NAME  = "sender-1"   # set by env WORKER_NAME
POLL_SECONDS = 5            # set by env OUTBOX_POLL_SECONDS


def _coerce_payload(p):
    if p is None:
        return {}
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p)
    except Exception:
        return {}


def _log_statement_events(db: Session, user_id: int, customer_id: int):
    invs = (
        db.query(Invoice)
          .filter(
              Invoice.user_id == user_id,
              Invoice.customer_id == customer_id,
              Invoice.status.in_(["open", "chasing", "partial"])
          )
          .all()
    )
    now = datetime.utcnow()
    for inv in invs:
        db.add(ReminderEvent(
            invoice_id=inv.id,
            channel="email",
            template="statement",
            sent_at=now,
            meta=json.dumps({"customer_id": customer_id, "statement": True})
        ))
    return len(invs)


def _next_backoff_minutes(attempts: int) -> int:
    return min(60, 2 ** max(0, attempts - 1))


def _maybe_mark_run_done(db: Session, run: StatementRun):
    if run and run.jobs_enqueued is not None:
        done = (run.jobs_succeeded or 0) + (run.jobs_failed or 0)
        if done >= (run.jobs_enqueued or 0):
            run.status = "done"
            run.run_finished_at = datetime.utcnow()


def _requeue_stale_processing(db: Session, stale_seconds: int = 120) -> int:
    """
    Convert 'processing' rows back to 'queued' if they've been stuck longer than N seconds.
    Protects against crashes between claim and send.
    """
    q = text("""
        UPDATE email_outbox
           SET status='queued',
               next_attempt_at = UTC_TIMESTAMP(),
               lock_owner = NULL,
               lock_acquired_at = NULL
         WHERE status='processing'
           AND lock_acquired_at IS NOT NULL
           AND lock_acquired_at < UTC_TIMESTAMP() - INTERVAL :secs SECOND
    """)
    res = db.execute(q, {"secs": stale_seconds})
    db.commit()
    return res.rowcount or 0


def _claim_one_due_job(db: Session) -> EmailOutbox | None:
    """
    Claim exactly one due job (SKIP LOCKED) and mark it processing.
    """
    jobs = (
        db.query(EmailOutbox)
          .filter(
              EmailOutbox.status == "queued",
              EmailOutbox.next_attempt_at <= datetime.utcnow(),
              EmailOutbox.channel.in_(["email", "sms"]),
          )
          .order_by(EmailOutbox.id.asc())
          .with_for_update(skip_locked=True)
          .limit(1)
          .all()
    )

    if not jobs:
        log.info("no claimable jobs at this tick")
        return None

    j = jobs[0]
    log.info("claimed job id=%s (template=%s) next_at=%s", j.id, j.template, j.next_attempt_at)

    j.status = "processing"
    j.lock_owner = WORKER_NAME
    j.lock_acquired_at = datetime.utcnow()
    db.commit()
    return j


# ---------- NEW: preflight check for account email settings ----------
def _preflight_email_settings_or_fail(db: Session, job: EmailOutbox) -> bool:
    """
    Ensure the account_email_settings row for this job's user exists and is usable.
    If not usable, mark the job 'failed' permanently with a precise message and return False.
    Returns True if settings look good to proceed.
    """
    # Read mode + presence of encrypted token
    row = db.execute(
        text("""
            SELECT mode, postmark_server_token_enc
              FROM account_email_settings
             WHERE user_id = :uid
             LIMIT 1
        """),
        {"uid": job.user_id},
    ).first()

    if not row:
        job.attempt_count = (job.attempt_count or 0) + 1
        job.status = "failed"
        job.last_error = "Email settings not configured for this account (no account_email_settings row)"
        job.lock_owner = None
        job.lock_acquired_at = None
        db.commit()
        log.error("Job %s failed: %s", job.id, job.last_error)
        return False

    mode = (row.mode or "").strip()

    if mode == "custom_domain":
        if not row.postmark_server_token_enc:
            job.attempt_count = (job.attempt_count or 0) + 1
            job.status = "failed"
            job.last_error = "Email settings incomplete: custom domain selected but no encrypted Postmark server token"
            job.lock_owner = None
            job.lock_acquired_at = None
            db.commit()
            log.error("Job %s failed: %s", job.id, job.last_error)
            return False
        return True

    # platform mode → must have env var set (matches mailer._resolve_sender_and_token)
    if mode == "platform":
        if not (os.getenv("POSTMARK_SERVER_TOKEN_DEFAULT", "").strip()):
            job.attempt_count = (job.attempt_count or 0) + 1
            job.status = "failed"
            job.last_error = "Server misconfiguration: POSTMARK_SERVER_TOKEN_DEFAULT is not set for platform mode"
            job.lock_owner = None
            job.lock_acquired_at = None
            db.commit()
            log.error("Job %s failed: %s", job.id, job.last_error)
            return False
        return True

    # Unknown mode
    job.attempt_count = (job.attempt_count or 0) + 1
    job.status = "failed"
    job.last_error = f"Email settings invalid mode: {mode!r}"
    job.lock_owner = None
    job.lock_acquired_at = None
    db.commit()
    log.error("Job %s failed: %s", job.id, job.last_error)
    return False


def _preflight_sms_settings_or_fail(db: Session, job: EmailOutbox) -> bool:
    row = (
        db.query(AccountSmsSettings)
        .filter(AccountSmsSettings.user_id == job.user_id)
        .first()
    )
    if not row or not row.enabled:
        job.attempt_count = (job.attempt_count or 0) + 1
        job.status = "failed"
        job.last_error = "SMS settings not enabled for this account"
        job.lock_owner = None
        job.lock_acquired_at = None
        db.commit()
        log.error("Job %s failed: %s", job.id, job.last_error)
        return False
    if not row.twilio_phone_number or not row.twilio_subaccount_sid:
        job.attempt_count = (job.attempt_count or 0) + 1
        job.status = "failed"
        job.last_error = "SMS settings incomplete: missing Twilio phone number or subaccount SID"
        job.lock_owner = None
        job.lock_acquired_at = None
        db.commit()
        log.error("Job %s failed: %s", job.id, job.last_error)
        return False
    return True


def _twilio_auth_headers(username: str, password: str) -> tuple[str, str]:
    return (username, password)


def _twilio_request_with_fallback(
    method: str,
    url: str,
    *,
    primary_auth: tuple[str, str],
    fallback_auth: tuple[str, str] | None = None,
    data: dict | None = None,
    timeout: int = 20,
):
    response = requests.request(
        method,
        url,
        data=data,
        auth=primary_auth,
        timeout=timeout,
    )
    if response.status_code == 401 and fallback_auth and fallback_auth != primary_auth:
        response = requests.request(
            method,
            url,
            data=data,
            auth=fallback_auth,
            timeout=timeout,
        )
    return response


def _send_sms_via_twilio(db: Session, job: EmailOutbox) -> str:
    settings = (
        db.query(AccountSmsSettings)
        .filter(AccountSmsSettings.user_id == job.user_id)
        .first()
    )
    if not settings:
        raise RuntimeError("SMS settings missing for user")

    to_number = (job.to_email or "").strip()
    if not to_number:
        raise RuntimeError("Missing recipient phone number")

    from_number = (settings.twilio_phone_number or "").strip()
    if not from_number:
        raise RuntimeError("Missing Twilio sender phone number")

    account_sid = (settings.twilio_subaccount_sid or "").strip()
    if not account_sid:
        raise RuntimeError("Missing Twilio subaccount SID")

    api_key_sid = (os.getenv("TWILIO_API_KEY_SID", "") or "").strip()
    api_key_secret = (os.getenv("TWILIO_API_KEY_SECRET", "") or "").strip()
    if not api_key_sid or not api_key_secret:
        raise RuntimeError("Twilio API key credentials not configured")

    master_sid = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
    master_auth_token = (os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()

    primary_auth = _twilio_auth_headers(api_key_sid, api_key_secret)
    fallback_auth = (
        _twilio_auth_headers(master_sid, master_auth_token)
        if master_sid and master_auth_token
        else None
    )

    send_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    payload = {
        "From": from_number,
        "To": to_number,
        "Body": job.body or "",
    }
    r_send = _twilio_request_with_fallback(
        "POST",
        send_url,
        data=payload,
        primary_auth=primary_auth,
        fallback_auth=fallback_auth,
    )
    if not r_send.ok:
        raise RuntimeError(f"Twilio send failed: {r_send.status_code} {r_send.text}")
    data = r_send.json() or {}
    return str(data.get("sid") or "")


def process_once() -> int:
    db: Session = SessionLocal()

    # heartbeat
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        log.error("DB heartbeat failed:\n%s", traceback.format_exc())
        db.close()
        return 0

    # safety net: requeue stale processing rows
    try:
        fixed = _requeue_stale_processing(db, stale_seconds=120)
        if fixed:
            log.warning("Requeued %d stale processing job(s)", fixed)
    except Exception:
        log.error("Failed to requeue stale rows:\n%s", traceback.format_exc())

    sent_count = 0
    claimed_any = False

    # process up to BATCH_SIZE per pass, one claim at a time
    due_cnt = db.query(EmailOutbox.id).filter(
        EmailOutbox.status == "queued",
        EmailOutbox.channel.in_(["email", "sms"]),
        EmailOutbox.next_attempt_at <= datetime.utcnow(),
    ).count()
    log.info("due jobs right now = %s", due_cnt)
    for _ in range(BATCH_SIZE):
        try:
            j = _claim_one_due_job(db)
            if not j:
                break
            claimed_any = True

            log.info("Processing job id=%s channel=%s to=%s subj=%r", j.id, j.channel, j.to_email, (j.subject or "")[:80])

            try:
                user = db.query(User).filter(User.id == j.user_id).first()
                cust = db.query(Customer).filter(Customer.id == j.customer_id).first() if j.customer_id else None
                if not user or (j.customer_id and not cust):
                    raise RuntimeError("Missing user or customer")

                if j.channel == "sms":
                    if not _preflight_sms_settings_or_fail(db, j):
                        continue
                    message_sid = _send_sms_via_twilio(db, j)
                    if message_sid:
                        j.provider_message_id = message_sid
                else:
                    # ---------- NEW: validate email settings before sending ----------
                    if not _preflight_email_settings_or_fail(db, j):
                        # already marked failed with clear message
                        continue

                    payload = _coerce_payload(j.payload_json)
                    log.info("Sending via Postmark… outbox_id=%s", j.id)

                    # ---- call sender
                    tmpl = (j.template or "").lower()
                    if tmpl == "statement":
                        res = send_statement_for_user(
                            db=db,
                            user_id=user.id,
                            to_email=j.to_email,
                            subject=j.subject,
                            message=j.body,
                            payload_json=payload,
                            customer_name=cust.name if cust else None,
                        )
                    else:
                        # chasing (or any non-statement template)
                        res = send_chasing_for_user(
                            db=db,
                            user_id=user.id,
                            to_email=j.to_email,
                            subject=j.subject or "",
                            html_body=j.body or "",
                        )

                    if not res.ok:
                        # If permanent, fail immediately (no retry)
                        if getattr(res, "permanent", False):
                            j.attempt_count = (j.attempt_count or 0) + 1
                            j.status = "failed"
                            j.last_error = (getattr(res, "error", "") or "")[:2000]
                            j.lock_owner = None
                            j.lock_acquired_at = None
                            if j.run_id:
                                run = db.query(StatementRun).get(j.run_id)
                                if run:
                                    if run.run_started_at is None:
                                        run.run_started_at = datetime.utcnow()
                                        run.status = "processing"
                                    run.jobs_failed = (run.jobs_failed or 0) + 1
                                    _maybe_mark_run_done(db, run)
                            db.commit()
                            log.warning("Job %s marked failed permanently (code=%s): %s",
                                        j.id, getattr(res, "code", None), getattr(res, "error", None))
                            continue

                        # transient error → let the generic retry path handle it via except
                        raise RuntimeError(getattr(res, "error", None) or "Mail send failed")

                    # ---- success — provider accepted
                    if (j.template or "").lower() == "statement":
                        _log_statement_events(db, j.user_id, j.customer_id)

                    j.provider = "postmark"
                    if res.message_id:
                        j.provider_message_id = str(res.message_id)

                j.status = "sent"
                j.delivery_status = "sent"
                j.updated_at = datetime.utcnow()
                j.lock_owner = None
                j.lock_acquired_at = None
                sent_count += 1

                if j.run_id:
                    run = db.query(StatementRun).filter(StatementRun.id == j.run_id).first()
                    if run:
                        if run.run_started_at is None:
                            run.run_started_at = datetime.utcnow()
                            run.status = "processing"
                        run.jobs_succeeded = (run.jobs_succeeded or 0) + 1
                        _maybe_mark_run_done(db, run)

                db.commit()
                log.info("Job %s sent; provider_msg_id=%s", j.id, j.provider_message_id or "-")

            except Exception:
                # per-job failure; requeue with backoff or mark failed
                db.rollback()
                err = traceback.format_exc()
                log.error("Job %s error:\n%s", j.id, err)

                j = db.query(EmailOutbox).get(j.id)  # refresh managed instance
                j.attempt_count = (j.attempt_count or 0) + 1
                j.last_error = err[:2000]

                if j.run_id:
                    run = db.query(StatementRun).get(j.run_id)
                    if run and j.attempt_count >= MAX_ATTEMPTS:
                        run.jobs_failed = (run.jobs_failed or 0) + 1
                        _maybe_mark_run_done(db, run)

                if j.attempt_count >= MAX_ATTEMPTS:
                    j.status = "failed"
                    j.lock_owner = None
                    j.lock_acquired_at = None
                    log.error("Job %s failed permanently after %d attempts", j.id, j.attempt_count)
                else:
                    backoff_min = _next_backoff_minutes(j.attempt_count)
                    j.status = "queued"
                    j.next_attempt_at = datetime.utcnow() + timedelta(minutes=backoff_min)
                    j.lock_owner = None
                    j.lock_acquired_at = None
                    log.warning("Job %s requeued with backoff=%d min (attempt %d)", j.id, backoff_min, j.attempt_count)

                db.commit()

        except Exception:
            # claim-level error; keep loop alive
            log.error("Claim/send loop error:\n%s", traceback.format_exc())
            time.sleep(1)

    db.close()
    if not claimed_any:
        return 0
    return sent_count


def run_forever(sleep_seconds: int = 5):
    log.info("worker starting (poll=%ss, worker=%s, batch=%s, max_attempts=%s)",
             sleep_seconds, WORKER_NAME, BATCH_SIZE, MAX_ATTEMPTS)
    while True:
        try:
            n = process_once()
            if n == 0:
                time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            log.info("worker stopped by user")
            break
        except Exception:
            log.error("unexpected loop error:\n%s", traceback.format_exc())
            time.sleep(2)


# ---------- module entrypoint so `-m app.routers.outbox_worker` runs ----------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except Exception:
        return default

if __name__ == "__main__":
    WORKER_NAME  = os.getenv("WORKER_NAME", WORKER_NAME)
    BATCH_SIZE   = _env_int("OUTBOX_BATCH_SIZE", BATCH_SIZE)
    MAX_ATTEMPTS = _env_int("OUTBOX_MAX_ATTEMPTS", MAX_ATTEMPTS)
    POLL_SECONDS = _env_int("OUTBOX_POLL_SECONDS", POLL_SECONDS)

    dry = os.getenv("OUTBOX_DRY_RUN", "0").strip() in ("1", "true", "yes")

    log.info("boot env: WORKER_NAME=%s BATCH_SIZE=%s MAX_ATTEMPTS=%s POLL=%ss DRY_RUN=%s",
             WORKER_NAME, BATCH_SIZE, MAX_ATTEMPTS, POLL_SECONDS, dry)

    run_forever(sleep_seconds=POLL_SECONDS)
