import hashlib
import json
import os
from datetime import datetime, timezone, timedelta

from fastapi import Request

from ..database import get_db
from ..models import AccountSmsSettings, SmsCreditLedger, AccountBillingProfile
from ..shared import APIRouter, BaseModel, Depends, HTTPException, Session
from .auth import require_user

router = APIRouter(prefix="/api/billing/stripe", tags=["stripe_billing"])

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PUBLIC_BASE_URL = os.getenv("APP_BASE_URL", "https://app.remindandpay.com").rstrip("/")
_SUBSCRIPTION_PRICE_ID = os.getenv("STRIPE_STARTER_SUBSCRIPTION_PRICE_ID", "")
_TOPUP_RETRY_MAX_ATTEMPTS = 5

_TOPUP_PACKAGES = {
    "10": {"price_id": os.getenv("STRIPE_SMS_TOPUP_10_PRICE_ID", ""), "credits": 1000},
    "25": {"price_id": os.getenv("STRIPE_SMS_TOPUP_25_PRICE_ID", ""), "credits": 2500},
    "50": {"price_id": os.getenv("STRIPE_SMS_TOPUP_50_PRICE_ID", ""), "credits": 5000},
    "100": {"price_id": os.getenv("STRIPE_SMS_TOPUP_100_PRICE_ID", ""), "credits": 10000},
}


class CheckoutSessionIn(BaseModel):
    package_key: str


@router.post("/checkout-session")
def create_checkout_session(
    payload: CheckoutSessionIn,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    package_key = str(payload.package_key)
    package = _TOPUP_PACKAGES.get(package_key)
    if not package:
        raise HTTPException(status_code=400, detail="Invalid top-up package")

    price_id = package.get("price_id")
    if not price_id:
        raise HTTPException(status_code=500, detail=f"Missing Stripe price ID for package {package_key}")

    if not _STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY is not configured")

    stripe_client = _get_stripe_client()

    topup_metadata = {
        "user_id": str(user.id),
        "credits": str(package["credits"]),
        "kind": "sms_topup",
        "package_key": package_key,
    }

    session_kwargs = {
        "mode": "payment",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{_PUBLIC_BASE_URL}/sms_billing?topup=success",
        "cancel_url": f"{_PUBLIC_BASE_URL}/sms_billing?topup=cancelled",
        "metadata": topup_metadata,
        "payment_intent_data": {"metadata": topup_metadata},
        # Ensure one-off topups generate Stripe invoices so they appear in the billing history UI.
        "invoice_creation": {
            "enabled": True,
            "invoice_data": {
                "metadata": topup_metadata,
                "description": f"SMS credit top-up ({package['credits']} credits)",
            },
        },
    }
    profile = db.query(AccountBillingProfile).filter(AccountBillingProfile.user_id == user.id).first()
    customer_id = str(getattr(profile, "stripe_customer_id", "") or "").strip() if profile else ""
    user_email = getattr(user, "email", None)
    if customer_id:
        session_kwargs["customer"] = customer_id
    else:
        if not user_email:
            raise HTTPException(status_code=400, detail="Logged-in user is missing an email address")
        session_kwargs["customer_email"] = user_email

    session = stripe_client.checkout.Session.create(**session_kwargs)

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/subscription-checkout-session")
def create_subscription_checkout_session(
    user=Depends(require_user),
):
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY is not configured")
    if not _SUBSCRIPTION_PRICE_ID:
        raise HTTPException(status_code=500, detail="STRIPE_STARTER_SUBSCRIPTION_PRICE_ID is not configured")

    stripe_client = _get_stripe_client()

    session = stripe_client.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": _SUBSCRIPTION_PRICE_ID, "quantity": 1}],
        success_url=f"{_PUBLIC_BASE_URL}/settings#billing",
        cancel_url=f"{_PUBLIC_BASE_URL}/settings#billing",
        metadata={
            "user_id": str(user.id),
            "kind": "membership_subscription",
        },
        subscription_data={
            "metadata": {
                "user_id": str(user.id),
                "kind": "membership_subscription",
            }
        },
        customer_email=user.email,
    )
    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not _WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET is not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe signature")

    try:
        stripe_client = _get_stripe_client()
        event = stripe_client.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception as exc:
        if exc.__class__.__name__ != "SignatureVerificationError":
            raise
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    if event_type == "checkout.session.completed":
        _handle_checkout_completed(db, event)
        _handle_subscription_checkout_completed(db, event)
    elif event_type == "payment_intent.succeeded":
        _handle_payment_intent_succeeded(db, event)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(db, event)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(db, event)
    elif event_type == "invoice.paid":
        _handle_invoice_paid(db, event)
    elif event_type == "invoice.payment_failed":
        _handle_invoice_payment_failed(db, event)
    elif event_type == "charge.refunded":
        _handle_charge_refunded(db, event)
    elif event_type in {"payment_intent.payment_failed"}:
        pass

    return {"ok": True}


@router.post("/topup-reconcile/enqueue-due")
def enqueue_due_topup_reconcile(db: Session = Depends(get_db)):
    """Scheduler-triggered bounded retry pass for unresolved top-up invoice linkage."""
    stripe_client = _get_stripe_client()
    if not stripe_client.api_key:
        return {"ok": False, "error": "STRIPE_SECRET_KEY not configured"}

    now = datetime.now(timezone.utc)
    batch_limit = max(1, min(int(os.getenv("TOPUP_RECONCILE_BATCH_LIMIT", "100")), 500))

    rows = (
        db.query(SmsCreditLedger)
        .filter(SmsCreditLedger.reason == "stripe_topup")
        .order_by(SmsCreditLedger.created_at.asc())
        .limit(batch_limit * 3)
        .all()
    )

    scanned = 0
    resolved = 0
    marked_anomaly = 0
    updated = 0

    for row in rows:
        details = dict(row.details) if isinstance(row.details, dict) else {}
        if details.get("stripe_invoice_id"):
            continue

        status = str(details.get("invoice_reconcile_status") or "pending")
        retry_count = int(details.get("invoice_retry_count") or 0)
        max_attempts = int(details.get("invoice_retry_max_attempts") or _TOPUP_RETRY_MAX_ATTEMPTS)
        next_retry_raw = details.get("next_retry_at")

        if status == "anomaly" or retry_count >= max_attempts:
            continue

        if next_retry_raw:
            try:
                due_at = datetime.fromisoformat(str(next_retry_raw).replace("Z", "+00:00"))
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=timezone.utc)
            except Exception:
                due_at = now
            if due_at > now:
                continue

        scanned += 1
        payment_intent_id = str(details.get("payment_intent_id") or "").strip()
        found = _resolve_topup_invoice_details(stripe_client, payment_intent_id)

        if found.get("stripe_invoice_id"):
            details.update(found)
            details["invoice_reconcile_status"] = "resolved"
            details["next_retry_at"] = None
            resolved += 1
        else:
            retry_count += 1
            details["invoice_retry_count"] = retry_count
            details["invoice_retry_max_attempts"] = max_attempts
            if retry_count >= max_attempts:
                details["invoice_reconcile_status"] = "anomaly"
                details["next_retry_at"] = None
                marked_anomaly += 1
            else:
                details["invoice_reconcile_status"] = "pending"
                details["next_retry_at"] = (now + _retry_delay_for_attempt(retry_count)).isoformat()

        row.details = details
        db.add(row)
        updated += 1

        if scanned >= batch_limit:
            break

    if updated:
        db.commit()

    return {
        "ok": True,
        "scanned": scanned,
        "resolved": resolved,
        "marked_anomaly": marked_anomaly,
        "updated": updated,
    }


def _handle_charge_refunded(db: Session, event: dict):
    obj = event["data"]["object"]
    payment_intent_id = _stripe_obj_id(getattr(obj, "payment_intent", None))
    refund_id = _stripe_obj_id(getattr(obj, "id", None))
    if not payment_intent_id or not refund_id:
        return

    # Idempotency: one compensating debit per refund event.
    refund_ref = f"stripe:refund:{refund_id}"
    if db.query(SmsCreditLedger).filter(SmsCreditLedger.reference_id == refund_ref).first():
        return

    credits = (
        db.query(SmsCreditLedger)
        .filter(SmsCreditLedger.reason == "stripe_topup")
        .all()
    )
    matched = []
    for row in credits:
        details = dict(row.details) if isinstance(row.details, dict) else {}
        if str(details.get("payment_intent_id") or "").strip() == payment_intent_id and row.entry_type == "credit":
            matched.append(row)
    if not matched:
        return

    total_credits = sum(int(r.amount or 0) for r in matched)
    if total_credits <= 0:
        return

    amount_refunded = int(getattr(obj, "amount_refunded", 0) or 0)
    amount_captured = int(getattr(obj, "amount_captured", 0) or 0)
    if amount_refunded <= 0:
        return

    invoice_details = _resolve_topup_invoice_details(_get_stripe_client(), payment_intent_id)

    # Full refund -> full credit reversal; partial refund -> proportional reversal.
    if amount_captured > 0 and amount_refunded < amount_captured:
        to_reverse = max(1, int((total_credits * amount_refunded) / amount_captured))
    else:
        to_reverse = total_credits

    user_id = matched[0].user_id
    db.add(
        SmsCreditLedger(
            user_id=user_id,
            entry_type="debit",
            amount=to_reverse,
            reason="stripe_refund_reversal",
            reference_id=refund_ref,
            details={
                "stripe_event_id": event["id"],
                "payment_intent_id": payment_intent_id,
                "stripe_charge_id": _stripe_obj_id(getattr(obj, "charge", None)),
                "stripe_refund_id": refund_id,
                "stripe_invoice_id": invoice_details.get("stripe_invoice_id"),
                "stripe_invoice_number": invoice_details.get("stripe_invoice_number"),
                "amount_refunded": amount_refunded,
                "amount_captured": amount_captured,
                "source": "stripe_webhook_refund",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    )
    db.commit()


def _handle_checkout_completed(db: Session, event: dict):
    obj = event["data"]["object"]
    if getattr(obj, "mode", None) != "payment":
        return

    metadata = _metadata_to_dict(getattr(obj, "metadata", None))
    if metadata.get("kind") != "sms_topup":
        return

    user_id_str = metadata.get("user_id")
    if not user_id_str:
        return

    try:
        user_id = int(user_id_str)
    except (TypeError, ValueError):
        return

    settings = db.query(AccountSmsSettings).filter(AccountSmsSettings.user_id == user_id).first()
    if not settings:
        return

    payment_intent_id = str(getattr(obj, "payment_intent", "") or "").strip()
    checkout_session_id = str(obj["id"])
    reference_id = _sms_topup_reference_id(payment_intent_id=payment_intent_id, checkout_session_id=checkout_session_id)
    if _topup_already_recorded(db, payment_intent_id=payment_intent_id, checkout_session_id=checkout_session_id):
        return

    amount = _credits_for_checkout_session(metadata)
    if amount <= 0:
        return

    invoice_details = _resolve_topup_invoice_details(_get_stripe_client(), payment_intent_id)

    db.add(
        SmsCreditLedger(
            user_id=user_id,
            entry_type="credit",
            amount=amount,
            reason="stripe_topup",
            reference_id=reference_id,
            details={
                "stripe_event_id": event["id"],
                "stripe_session_id": checkout_session_id,
                "payment_intent_id": payment_intent_id or None,
                "source": "stripe_webhook",
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "package_key": metadata.get("package_key"),
                **invoice_details,
            },
        )
    )
    db.commit()


def _handle_payment_intent_succeeded(db: Session, event: dict):
    obj = event["data"]["object"]
    metadata = _metadata_to_dict(getattr(obj, "metadata", None))
    if metadata.get("kind") != "sms_topup":
        return

    user_id_str = metadata.get("user_id")
    if not user_id_str:
        return
    try:
        user_id = int(user_id_str)
    except (TypeError, ValueError):
        return

    settings = db.query(AccountSmsSettings).filter(AccountSmsSettings.user_id == user_id).first()
    if not settings:
        return

    payment_intent_id = str(obj["id"])
    reference_id = _sms_topup_reference_id(payment_intent_id=payment_intent_id)
    if _topup_already_recorded(db, payment_intent_id=payment_intent_id):
        return

    amount = _credits_for_checkout_session(metadata)
    if amount <= 0:
        return

    invoice_details = _resolve_topup_invoice_details(_get_stripe_client(), payment_intent_id)

    db.add(
        SmsCreditLedger(
            user_id=user_id,
            entry_type="credit",
            amount=amount,
            reason="stripe_topup",
            reference_id=reference_id,
            details={
                "stripe_event_id": event["id"],
                "payment_intent_id": obj["id"],
                "source": "stripe_webhook_payment_intent",
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "package_key": metadata.get("package_key"),
                **invoice_details,
            },
        )
    )
    db.commit()


def _stripe_obj_id(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()
    return str(getattr(value, "id", "") or "").strip()


def _resolve_topup_invoice_details(stripe_client, payment_intent_id: str) -> dict:
    payment_intent_id = (payment_intent_id or "").strip()
    if not payment_intent_id:
        return {}

    invoice_id = ""
    inv = None

    # Most reliable for one-off top-ups: ask Stripe invoices directly by PI id.
    try:
        lst = stripe_client.Invoice.list(payment_intent=payment_intent_id, limit=1)
        items = getattr(lst, "data", None) or []
        if items:
            inv = items[0]
            invoice_id = _stripe_obj_id(inv)
    except Exception:
        pass

    # Fallback path if list() did not return an invoice.
    if not invoice_id:
        try:
            pi = stripe_client.PaymentIntent.retrieve(payment_intent_id)
        except Exception:
            pi = None

        if pi is not None:
            invoice_id = _stripe_obj_id(getattr(pi, "invoice", None))
            if not invoice_id:
                charge_id = _stripe_obj_id(getattr(pi, "latest_charge", None))
                if charge_id:
                    try:
                        ch = stripe_client.Charge.retrieve(charge_id)
                        invoice_id = _stripe_obj_id(getattr(ch, "invoice", None))
                    except Exception:
                        pass

    if not invoice_id:
        return {
            "invoice_reconcile_status": "pending",
            "invoice_retry_count": 0,
            "invoice_retry_max_attempts": _TOPUP_RETRY_MAX_ATTEMPTS,
            "next_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        }
    if inv is None:
        try:
            inv = stripe_client.Invoice.retrieve(invoice_id)
        except Exception:
            return {"stripe_invoice_id": invoice_id}
    return {
        "stripe_invoice_id": invoice_id,
        "stripe_invoice_number": getattr(inv, "number", None),
        "stripe_invoice_pdf": getattr(inv, "invoice_pdf", None),
        "stripe_invoice_url": getattr(inv, "hosted_invoice_url", None),
        "invoice_reconcile_status": "resolved",
        "invoice_retry_count": 0,
        "invoice_retry_max_attempts": _TOPUP_RETRY_MAX_ATTEMPTS,
        "next_retry_at": None,
    }


def _retry_delay_for_attempt(attempt: int) -> timedelta:
    schedule = {
        1: timedelta(minutes=15),
        2: timedelta(hours=1),
        3: timedelta(hours=4),
        4: timedelta(hours=12),
        5: timedelta(hours=24),
    }
    return schedule.get(attempt, timedelta(hours=24))


def _topup_already_recorded(db: Session, *, payment_intent_id: str, checkout_session_id: str = "") -> bool:
    payment_intent_id = (payment_intent_id or "").strip()
    checkout_session_id = (checkout_session_id or "").strip()

    if payment_intent_id:
        candidate_refs = {
            f"stripe:pi:{payment_intent_id}",
            f"stripe:payment_intent:{payment_intent_id}",
        }
        if db.query(SmsCreditLedger).filter(SmsCreditLedger.reference_id.in_(candidate_refs)).first():
            return True

        rows = (
            db.query(SmsCreditLedger)
            .filter(SmsCreditLedger.reason == "stripe_topup")
            .filter(SmsCreditLedger.details.isnot(None))
            .all()
        )
        for row in rows:
            details = dict(row.details) if isinstance(row.details, dict) else {}
            if str(details.get("payment_intent_id") or "").strip() == payment_intent_id:
                return True

    if checkout_session_id:
        rows = (
            db.query(SmsCreditLedger)
            .filter(SmsCreditLedger.reason == "stripe_topup")
            .filter(SmsCreditLedger.details.isnot(None))
            .all()
        )
        for row in rows:
            details = dict(row.details) if isinstance(row.details, dict) else {}
            if str(details.get("stripe_session_id") or "").strip() == checkout_session_id:
                return True

    return False


def _find_billing_profile(db: Session, *, user_id: int | None = None, stripe_customer_id: str | None = None, stripe_subscription_id: str | None = None):
    q = db.query(AccountBillingProfile)
    if user_id is not None:
        row = q.filter(AccountBillingProfile.user_id == user_id).first()
        if row:
            return row
    if stripe_subscription_id:
        row = q.filter(AccountBillingProfile.stripe_subscription_id == stripe_subscription_id).first()
        if row:
            return row
    if stripe_customer_id:
        return q.filter(AccountBillingProfile.stripe_customer_id == stripe_customer_id).first()
    return None


def _handle_subscription_checkout_completed(db: Session, event: dict):
    obj = event["data"]["object"]
    if getattr(obj, "mode", None) != "subscription":
        return
    metadata = _metadata_to_dict(getattr(obj, "metadata", None))
    if metadata.get("kind") != "membership_subscription":
        return
    try:
        user_id = int(metadata.get("user_id"))
    except (TypeError, ValueError):
        return

    profile = _find_billing_profile(db, user_id=user_id)
    if not profile:
        return

    profile.subscription_status = "active"
    profile.stripe_customer_id = str(getattr(obj, "customer", "") or "") or profile.stripe_customer_id
    profile.stripe_subscription_id = str(getattr(obj, "subscription", "") or "") or profile.stripe_subscription_id
    db.add(profile)
    db.commit()


def _handle_subscription_updated(db: Session, event: dict):
    obj = event["data"]["object"]
    status = str(getattr(obj, "status", "") or "")
    stripe_subscription_id = str(getattr(obj, "id", "") or "")
    stripe_customer_id = str(getattr(obj, "customer", "") or "")

    profile = _find_billing_profile(db, stripe_subscription_id=stripe_subscription_id, stripe_customer_id=stripe_customer_id)
    if not profile:
        return
    profile.subscription_status = status or profile.subscription_status
    profile.stripe_subscription_id = stripe_subscription_id or profile.stripe_subscription_id
    profile.stripe_customer_id = stripe_customer_id or profile.stripe_customer_id
    db.add(profile)
    db.commit()


def _handle_subscription_deleted(db: Session, event: dict):
    obj = event["data"]["object"]
    stripe_subscription_id = str(getattr(obj, "id", "") or "")
    stripe_customer_id = str(getattr(obj, "customer", "") or "")
    profile = _find_billing_profile(db, stripe_subscription_id=stripe_subscription_id, stripe_customer_id=stripe_customer_id)
    if not profile:
        return
    profile.subscription_status = "canceled"
    db.add(profile)
    db.commit()


def _handle_invoice_paid(db: Session, event: dict):
    obj = event["data"]["object"]
    stripe_subscription_id = str(getattr(obj, "subscription", "") or "")
    stripe_customer_id = str(getattr(obj, "customer", "") or "")
    profile = _find_billing_profile(db, stripe_subscription_id=stripe_subscription_id, stripe_customer_id=stripe_customer_id)
    if not profile:
        return
    profile.subscription_status = "active"
    db.add(profile)
    db.commit()


def _handle_invoice_payment_failed(db: Session, event: dict):
    obj = event["data"]["object"]
    stripe_subscription_id = str(getattr(obj, "subscription", "") or "")
    stripe_customer_id = str(getattr(obj, "customer", "") or "")
    profile = _find_billing_profile(db, stripe_subscription_id=stripe_subscription_id, stripe_customer_id=stripe_customer_id)
    if not profile:
        return
    profile.subscription_status = "past_due"
    db.add(profile)
    db.commit()


def _sms_topup_reference_id(*, payment_intent_id: str = "", checkout_session_id: str = "") -> str:
    payment_intent_id = (payment_intent_id or "").strip()
    if payment_intent_id:
        return f"stripe:pi:{payment_intent_id}"

    checkout_session_id = (checkout_session_id or "").strip()
    if checkout_session_id:
        digest = hashlib.sha1(checkout_session_id.encode("utf-8")).hexdigest()[:16]
        return f"stripe:cs:{digest}"

    return "stripe:topup:unknown"


def _metadata_to_dict(raw_metadata) -> dict:
    if raw_metadata is None:
        return {}
    if isinstance(raw_metadata, dict):
        return raw_metadata

    if hasattr(raw_metadata, "to_dict"):
        converted = raw_metadata.to_dict()
        if isinstance(converted, dict):
            return converted

    if hasattr(raw_metadata, "items"):
        return {str(k): v for k, v in raw_metadata.items()}

    return {}


def _credits_for_checkout_session(metadata: dict) -> int:
    try:
        credits = int(metadata.get("credits", 0))
    except (TypeError, ValueError):
        return 0
    if credits <= 0:
        return 0
    return credits


def _get_stripe_client():
    import stripe

    stripe.api_key = _STRIPE_SECRET_KEY
    return stripe
