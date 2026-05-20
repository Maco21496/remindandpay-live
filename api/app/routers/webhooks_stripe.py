import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from fastapi import Request

from ..database import get_db
from ..models import AccountSmsSettings, SmsCreditLedger, AccountBillingProfile, AccountBillingTransaction
from ..shared import APIRouter, BaseModel, Depends, HTTPException, Session
from .auth import require_user

router = APIRouter(prefix="/api/billing/stripe", tags=["stripe_billing"])

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PUBLIC_BASE_URL = os.getenv("APP_BASE_URL", "https://app.remindandpay.com").rstrip("/")
_SUBSCRIPTION_PRICE_ID = os.getenv("STRIPE_STARTER_SUBSCRIPTION_PRICE_ID", "")
_TOPUP_RETRY_MAX_ATTEMPTS = 5
logger = logging.getLogger(__name__)

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

    credits = int(package["credits"])
    product_code = f"sms_topup_{credits}"

    topup_metadata = {
        "account_id": str(user.id),
        "user_id": str(user.id),
        "product_type": "sms_topup",
        "product_code": product_code,
        "quantity": str(credits),
        "amount_minor": str(int(package_key) * 100),
        "currency": "GBP",
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
    skipped_has_invoice = 0
    skipped_not_due = 0
    skipped_exhausted = 0
    debug_enabled = os.getenv("BILLING_RECONCILE_DEBUG", "0").lower() in {"1", "true", "yes"}

    for row in rows:
        details = dict(row.details) if isinstance(row.details, dict) else {}
        status = str(details.get("invoice_reconcile_status") or "pending")
        retry_count = int(details.get("invoice_retry_count") or 0)
        max_attempts = int(details.get("invoice_retry_max_attempts") or _TOPUP_RETRY_MAX_ATTEMPTS)
        next_retry_raw = details.get("next_retry_at")

        if status == "anomaly" or retry_count >= max_attempts:
            skipped_exhausted += 1
            continue

        if next_retry_raw:
            try:
                due_at = datetime.fromisoformat(str(next_retry_raw).replace("Z", "+00:00"))
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=timezone.utc)
            except Exception:
                due_at = now
            if due_at > now:
                skipped_not_due += 1
                continue

        scanned += 1
        payment_intent_id = str(details.get("payment_intent_id") or "").strip()
        if payment_intent_id:
            details["payment_verified"] = True
            details["invoice_reconcile_status"] = "verified"
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

    if debug_enabled:
        logger.warning("[billing_reconcile_summary] scanned=%s resolved=%s marked_anomaly=%s updated=%s skipped_has_invoice=%s skipped_not_due=%s skipped_exhausted=%s now=%s", scanned, resolved, marked_anomaly, updated, skipped_has_invoice, skipped_not_due, skipped_exhausted, now.isoformat())

    return {
        "ok": True,
        "scanned": scanned,
        "resolved": resolved,
        "marked_anomaly": marked_anomaly,
        "updated": updated,
        "debug": {
            "skipped_has_invoice": skipped_has_invoice,
            "skipped_not_due": skipped_not_due,
            "skipped_exhausted": skipped_exhausted,
            "now": now.isoformat(),
        },
    }


def _record_billing_transaction(db: Session, payload: dict) -> AccountBillingTransaction | None:
    idem_key = str(payload.get("idempotency_key") or "").strip()
    if not idem_key:
        return None
    existing = db.query(AccountBillingTransaction).filter(AccountBillingTransaction.idempotency_key == idem_key).first()
    if existing:
        return existing
    stripe_event_id = str(payload.get("stripe_event_id") or "").strip()
    if stripe_event_id:
        existing_evt = db.query(AccountBillingTransaction).filter(AccountBillingTransaction.stripe_event_id == stripe_event_id).first()
        if existing_evt:
            return existing_evt
    txn = AccountBillingTransaction(**payload)
    db.add(txn)
    db.flush()
    return txn


def _apply_sms_ledger_for_transaction(db: Session, txn: AccountBillingTransaction):
    if txn.sms_ledger_processed_at is not None or txn.status != "succeeded":
        return
    if txn.product_type != "sms_topup":
        txn.sms_ledger_processed_at = datetime.now(timezone.utc)
        db.add(txn)
        return
    entry_type = "credit" if txn.transaction_type == "payment" else "debit"
    reason = "billing_transaction_topup" if entry_type == "credit" else "billing_transaction_refund_reversal"
    ref = f"billing_txn:{txn.id}:{txn.transaction_type}"
    if db.query(SmsCreditLedger).filter(SmsCreditLedger.reference_id == ref).first():
        txn.sms_ledger_processed_at = datetime.now(timezone.utc)
        db.add(txn)
        return
    db.add(SmsCreditLedger(user_id=txn.user_id, entry_type=entry_type, amount=abs(int(txn.quantity or 0)), reason=reason, reference_id=ref, billing_transaction_id=txn.id, details={"billing_transaction_id": txn.id, "stripe_payment_intent_id": txn.stripe_payment_intent_id, "stripe_refund_id": txn.stripe_refund_id}))
    txn.sms_ledger_processed_at = datetime.now(timezone.utc)
    db.add(txn)


def _handle_charge_refunded(db: Session, event: dict):
    obj = event["data"]["object"]
    payment_intent_id = _stripe_obj_id(getattr(obj, "payment_intent", None))
    refund_id = _stripe_obj_id(getattr(obj, "id", None))
    if not payment_intent_id or not refund_id:
        return

    original = (
        db.query(AccountBillingTransaction)
        .filter(AccountBillingTransaction.transaction_type == "payment")
        .filter(AccountBillingTransaction.stripe_payment_intent_id == payment_intent_id)
        .first()
    )
    if not original:
        return

    amount_refunded = int(getattr(obj, "amount_refunded", 0) or 0)
    amount_captured = int(getattr(obj, "amount_captured", 0) or 0)
    quantity = original.quantity or 0
    if amount_refunded > 0 and amount_captured > 0 and amount_refunded < amount_captured and quantity:
        quantity = max(1, int((quantity * amount_refunded) / amount_captured))

    checkout_session_id = str(getattr(obj, "id", "") or "")
    txn = _record_billing_transaction(db, {
        "user_id": original.user_id,
        "initiated_by_user_id": None,
        "transaction_type": "refund",
        "product_type": original.product_type,
        "product_code": original.product_code,
        "description": f"Refund for transaction #{original.id}",
        "status": "succeeded",
        "amount_minor": -abs(amount_refunded),
        "currency": original.currency,
        "quantity": quantity,
        "parent_transaction_id": original.id,
        "stripe_customer_id": original.stripe_customer_id,
        "stripe_payment_intent_id": payment_intent_id,
        "stripe_charge_id": _stripe_obj_id(getattr(obj, "charge", None)),
        "stripe_refund_id": refund_id,
        "stripe_event_id": event["id"],
        "idempotency_key": f"stripe:refund:{refund_id}",
        "details": {"stripe_source": "charge.refunded"},
    })
    _apply_sms_ledger_for_transaction(db, txn)
    original.status = "refunded" if quantity >= (original.quantity or 0) else "partially_refunded"
    db.add(original)
    db.commit()



def _handle_checkout_completed(db: Session, event: dict):
    obj = event["data"]["object"]
    if getattr(obj, "mode", None) != "payment":
        return
    metadata = _metadata_to_dict(getattr(obj, "metadata", None))
    if metadata.get("kind") != "sms_topup":
        return
    try:
        user_id = int(metadata.get("user_id"))
    except (TypeError, ValueError):
        return
    payment_intent_id = str(getattr(obj, "payment_intent", "") or "").strip()
    if not payment_intent_id:
        return
    existing = db.query(AccountBillingTransaction).filter(AccountBillingTransaction.stripe_payment_intent_id == payment_intent_id, AccountBillingTransaction.transaction_type == "payment").first()
    if existing:
        return
    quantity = int(metadata.get("quantity") or metadata.get("credits") or 0)
    if quantity <= 0:
        return
    invoice_details = _resolve_topup_invoice_details(_get_stripe_client(), payment_intent_id)
    amount_minor = int(metadata.get("amount_minor") or 0)
    checkout_session_id = str(getattr(obj, "id", "") or "")
    txn = _record_billing_transaction(db, {
        "user_id": user_id,
        "initiated_by_user_id": user_id,
        "transaction_type": "payment",
        "product_type": metadata.get("product_type") or "sms_topup",
        "product_code": metadata.get("product_code") or f"sms_topup_{quantity}",
        "description": f"SMS top-up ({quantity} credits)",
        "status": "succeeded",
        "amount_minor": amount_minor,
        "currency": (metadata.get("currency") or "GBP").upper(),
        "quantity": quantity,
        "stripe_customer_id": _stripe_obj_id(getattr(obj, "customer", None)),
        "stripe_checkout_session_id": checkout_session_id,
        "stripe_payment_intent_id": payment_intent_id,
        "stripe_invoice_id": invoice_details.get("stripe_invoice_id"),
        "stripe_event_id": event["id"],
        "idempotency_key": f"stripe:checkout_session:{checkout_session_id}",
        "details": {"stripe_source": "checkout.session.completed", "package_key": metadata.get("package_key")},
    })
    _apply_sms_ledger_for_transaction(db, txn)
    db.commit()



def _handle_payment_intent_succeeded(db: Session, event: dict):
    obj = event["data"]["object"]
    metadata = _metadata_to_dict(getattr(obj, "metadata", None))
    if metadata.get("kind") != "sms_topup":
        return
    try:
        user_id = int(metadata.get("user_id"))
    except (TypeError, ValueError):
        return
    payment_intent_id = str(obj["id"])
    if db.query(AccountBillingTransaction).filter(AccountBillingTransaction.stripe_payment_intent_id == payment_intent_id, AccountBillingTransaction.transaction_type == "payment").first():
        return
    amount = _credits_for_checkout_session(metadata)
    if amount <= 0:
        return
    invoice_details = _resolve_topup_invoice_details(_get_stripe_client(), payment_intent_id)
    txn = _record_billing_transaction(db, {
        "user_id": user_id,
        "initiated_by_user_id": user_id,
        "transaction_type": "payment",
        "product_type": "sms_topup",
        "product_code": f"sms_topup_{amount}",
        "description": f"SMS top-up ({amount} credits)",
        "status": "succeeded",
        "amount_minor": int(getattr(obj, "amount_received", 0) or 0),
        "currency": str(getattr(obj, "currency", "gbp") or "gbp").upper(),
        "quantity": amount,
        "stripe_customer_id": _stripe_obj_id(getattr(obj, "customer", None)),
        "stripe_payment_intent_id": payment_intent_id,
        "stripe_invoice_id": invoice_details.get("stripe_invoice_id"),
        "stripe_event_id": event["id"],
        "idempotency_key": f"stripe:payment_intent:{payment_intent_id}:sms_topup",
        "details": {"stripe_source": "payment_intent.succeeded", "package_key": metadata.get("package_key")},
    })
    _apply_sms_ledger_for_transaction(db, txn)
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
            customer_id = _stripe_obj_id(getattr(pi, "customer", None))
            if customer_id:
                try:
                    lst = stripe_client.Invoice.list(customer=customer_id, limit=100)
                    items = getattr(lst, "data", None) or []
                    for candidate in items:
                        candidate_pi = _stripe_obj_id(getattr(candidate, "payment_intent", None))
                        if candidate_pi == payment_intent_id:
                            inv = candidate
                            invoice_id = _stripe_obj_id(candidate)
                            break

                        # Some Stripe shapes keep PI link only on the charge attached to invoice.
                        candidate_charge = _stripe_obj_id(getattr(candidate, "charge", None))
                        if candidate_charge:
                            try:
                                ch2 = stripe_client.Charge.retrieve(candidate_charge)
                                ch2_pi = _stripe_obj_id(getattr(ch2, "payment_intent", None))
                                if ch2_pi == payment_intent_id:
                                    inv = candidate
                                    invoice_id = _stripe_obj_id(candidate)
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

    if not invoice_id:
        # If PI exists and succeeded, treat as payment-verified even if no invoice object is linkable.
        pi_status = str(getattr(pi, "status", "") or "") if pi is not None else ""
        if pi is not None and pi_status == "succeeded":
            return {
                "invoice_reconcile_status": "verified_no_invoice",
                "payment_verified": True,
                "invoice_retry_count": 0,
                "invoice_retry_max_attempts": _TOPUP_RETRY_MAX_ATTEMPTS,
                "next_retry_at": None,
            }
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
