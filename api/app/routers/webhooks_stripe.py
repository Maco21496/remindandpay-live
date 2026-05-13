import json
import os
from datetime import datetime, timezone

from fastapi import Request

from ..database import get_db
from ..models import AccountSmsSettings, SmsCreditLedger
from ..shared import APIRouter, BaseModel, Depends, HTTPException, Session
from .auth import require_user

router = APIRouter(prefix="/api/billing/stripe", tags=["stripe_billing"])

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PUBLIC_BASE_URL = os.getenv("APP_BASE_URL", "https://app.remindandpay.com").rstrip("/")

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

    session_kwargs = {
        "mode": "payment",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{_PUBLIC_BASE_URL}/sms_billing?topup=success",
        "cancel_url": f"{_PUBLIC_BASE_URL}/sms_billing?topup=cancelled",
        "metadata": {
            "user_id": str(user.id),
            "credits": str(package["credits"]),
            "kind": "sms_topup",
            "package_key": package_key,
        },
        "payment_intent_data": {
            "metadata": {
                "user_id": str(user.id),
                "credits": str(package["credits"]),
                "kind": "sms_topup",
                "package_key": package_key,
            }
        },
    }
    user_email = getattr(user, "email", None)
    if not user_email:
        raise HTTPException(status_code=400, detail="Logged-in user is missing an email address")
    session_kwargs["customer_email"] = user_email

    session = stripe_client.checkout.Session.create(**session_kwargs)

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
    elif event_type == "payment_intent.succeeded":
        _handle_payment_intent_succeeded(db, event)
    elif event_type in {"payment_intent.payment_failed", "charge.refunded"}:
        pass

    return {"ok": True}


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

    reference_id = f"stripe:checkout_session:{obj['id']}"
    if db.query(SmsCreditLedger).filter(SmsCreditLedger.reference_id == reference_id).first():
        return

    amount = _credits_for_checkout_session(metadata)
    if amount <= 0:
        return

    db.add(
        SmsCreditLedger(
            user_id=user_id,
            entry_type="credit",
            amount=amount,
            reason="stripe_topup",
            reference_id=reference_id,
            details={
                "stripe_event_id": event["id"],
                "stripe_session_id": obj["id"],
                "payment_intent_id": getattr(obj, "payment_intent", None),
                "source": "stripe_webhook",
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "package_key": metadata.get("package_key"),
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

    reference_id = f"stripe:payment_intent:{obj['id']}"
    if db.query(SmsCreditLedger).filter(SmsCreditLedger.reference_id == reference_id).first():
        return

    amount = _credits_for_checkout_session(metadata)
    if amount <= 0:
        return

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
            },
        )
    )
    db.commit()


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
