from __future__ import annotations

import os
from email.utils import getaddresses
from typing import Mapping, Optional, Sequence


_ALLOWED_DELIVERY_TYPES = {"live": "Live", "sandbox": "Sandbox"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_value(env: Optional[Mapping[str, str]], name: str) -> str:
    source = os.environ if env is None else env
    return (source.get(name, "") or "").strip()


def is_staging_environment(env: Optional[Mapping[str, str]] = None) -> bool:
    return any(
        _env_value(env, name).lower() == "staging"
        for name in ("APP_ENV", "ENVIRONMENT")
    )


def resolve_postmark_delivery_type(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """
    Return the Postmark DeliveryType to include in server creation payloads.

    Explicit POSTMARK_DELIVERY_TYPE accepts only Live or Sandbox. Staging always
    resolves to Sandbox unless explicitly set to an invalid/unsafe value, in
    which case it fails loudly. Non-staging leaves DeliveryType omitted when the
    variable is unset so existing/live Postmark default behaviour is preserved.
    """
    explicit = _env_value(env, "POSTMARK_DELIVERY_TYPE")
    staging = is_staging_environment(env)
    if explicit:
        resolved = _ALLOWED_DELIVERY_TYPES.get(explicit.lower())
        if not resolved:
            raise ValueError("POSTMARK_DELIVERY_TYPE must be either 'Live' or 'Sandbox'")
        if staging and resolved != "Sandbox":
            raise ValueError("APP_ENV=staging requires POSTMARK_DELIVERY_TYPE=Sandbox")
        return resolved
    if staging:
        return "Sandbox"
    return None



def build_postmark_server_create_payload(name: str, env: Optional[Mapping[str, str]] = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "Name": name,
        "Color": "Blue",
        "SmtpApiActivated": True,
    }
    delivery_type = resolve_postmark_delivery_type(env)
    if delivery_type:
        payload["DeliveryType"] = delivery_type
    return payload

def resolve_app_base_url(env: Optional[Mapping[str, str]] = None) -> str:
    configured = _env_value(env, "APP_BASE_URL").rstrip("/")
    if configured:
        return configured
    if is_staging_environment(env):
        return "https://staging.remindandpay.com"
    return "https://app.remindandpay.com"


def resolve_postmark_webhook_url(env: Optional[Mapping[str, str]] = None) -> str:
    configured = _env_value(env, "POSTMARK_WEBHOOK_URL")
    if configured:
        return configured
    return f"{resolve_app_base_url(env)}/api/postmark/webhook"


def resolve_postmark_inbound_hook_url(env: Optional[Mapping[str, str]] = None) -> str:
    configured = _env_value(env, "POSTMARK_INBOUND_HOOK_URL")
    if configured:
        return configured
    return f"{resolve_app_base_url(env)}/api/postmark/inbound"


def staging_email_safety_is_configured(env: Optional[Mapping[str, str]] = None) -> bool:
    if not is_staging_environment(env):
        return True
    return bool(
        _env_value(env, "EMAIL_RECIPIENT_OVERRIDE")
        or _env_value(env, "EMAIL_ALLOWED_RECIPIENTS")
        or _env_value(env, "EMAIL_ALLOWED_DOMAINS")
    )


def validate_postmark_staging_config(env: Optional[Mapping[str, str]] = None) -> None:
    """
    Validate staging Postmark safety at app startup/runtime.

    If staging has any Postmark tokens configured, DeliveryType must resolve to
    Sandbox and recipient safety must be configured. The send layer still fails
    closed even if startup validation is bypassed.
    """
    if not is_staging_environment(env):
        return

    delivery_type = resolve_postmark_delivery_type(env)
    if delivery_type != "Sandbox":
        raise RuntimeError("Staging Postmark configuration must resolve to DeliveryType=Sandbox")

    postmark_enabled = any(
        _env_value(env, key)
        for key in (
            "POSTMARK_ACCOUNT_TOKEN",
            "POSTMARK_ACCOUNT_TOKEN_DEFAULT",
            "POSTMARK_SERVER_TOKEN_DEFAULT",
        )
    )
    if postmark_enabled and not staging_email_safety_is_configured(env):
        raise RuntimeError(
            "Staging Postmark sends require EMAIL_RECIPIENT_OVERRIDE or "
            "EMAIL_ALLOWED_RECIPIENTS/EMAIL_ALLOWED_DOMAINS"
        )


def _csv_values(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _recipient_addresses(to_value: str) -> list[str]:
    parsed = getaddresses([to_value or ""])
    return [email.strip().lower() for _, email in parsed if email.strip()]


def _domain_matches(address: str, allowed_domains: Sequence[str]) -> bool:
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].lower()
    return any(domain == allowed.lower().lstrip("@") for allowed in allowed_domains)


def apply_staging_email_safety(
    to_value: str,
    subject: str,
    env: Optional[Mapping[str, str]] = None,
) -> tuple[str, str]:
    """
    Apply staging-only outbound email safety.

    Live/non-staging returns values unchanged. Staging rewrites recipients when
    EMAIL_RECIPIENT_OVERRIDE is set, otherwise requires all recipients to match
    EMAIL_ALLOWED_RECIPIENTS or EMAIL_ALLOWED_DOMAINS. If staging has no override
    and no allow-list, fail closed before Postmark is called.
    """
    if not is_staging_environment(env):
        return to_value, subject

    prefix = _env_value(env, "EMAIL_SUBJECT_PREFIX")
    safe_subject = subject or ""
    if prefix and not safe_subject.startswith(prefix):
        safe_subject = f"{prefix} {safe_subject}".strip()

    override = _env_value(env, "EMAIL_RECIPIENT_OVERRIDE")
    if override:
        return override, safe_subject

    allowed_recipients = {value.lower() for value in _csv_values(_env_value(env, "EMAIL_ALLOWED_RECIPIENTS"))}
    allowed_domains = _csv_values(_env_value(env, "EMAIL_ALLOWED_DOMAINS"))
    if not allowed_recipients and not allowed_domains:
        raise ValueError(
            "Staging email safety blocked send: configure EMAIL_RECIPIENT_OVERRIDE "
            "or EMAIL_ALLOWED_RECIPIENTS/EMAIL_ALLOWED_DOMAINS"
        )

    recipients = _recipient_addresses(to_value)
    if not recipients:
        raise ValueError("Staging email safety blocked send: no valid recipient address")

    blocked = [
        address for address in recipients
        if address not in allowed_recipients and not _domain_matches(address, allowed_domains)
    ]
    if blocked:
        raise ValueError(
            "Staging email safety blocked send to non-allowed recipient(s): "
            + ", ".join(blocked)
        )

    return to_value, safe_subject


def outbox_dry_run_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return _env_value(env, "OUTBOX_DRY_RUN").lower() in _TRUE_VALUES
