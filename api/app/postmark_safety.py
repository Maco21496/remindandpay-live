from __future__ import annotations

import os
from email.utils import getaddresses
from typing import Mapping, Optional, Sequence


_ALLOWED_DELIVERY_TYPES = {"live": "Live", "sandbox": "Sandbox"}


def _env_value(env: Optional[Mapping[str, str]], name: str) -> str:
    source = os.environ if env is None else env
    return (source.get(name, "") or "").strip()


def is_staging_environment(env: Optional[Mapping[str, str]] = None) -> bool:
    return any(
        _env_value(env, name).lower() == "staging"
        for name in ("APP_ENV", "ENVIRONMENT")
    )


def resolve_postmark_delivery_type(env: Optional[Mapping[str, str]] = None) -> str:
    """
    Return the Postmark DeliveryType to include in server creation payloads.

    Explicit POSTMARK_DELIVERY_TYPE accepts only Live or Sandbox. When unset,
    staging defaults to Sandbox and every other environment keeps the existing
    live Postmark server creation behaviour.
    """
    explicit = _env_value(env, "POSTMARK_DELIVERY_TYPE")
    if explicit:
        resolved = _ALLOWED_DELIVERY_TYPES.get(explicit.lower())
        if not resolved:
            raise ValueError("POSTMARK_DELIVERY_TYPE must be either 'Live' or 'Sandbox'")
        return resolved
    if is_staging_environment(env):
        return "Sandbox"
    return "Live"


def postmark_server_creation_payload(name: str, env: Optional[Mapping[str, str]] = None) -> dict[str, object]:
    """Build the shared Postmark server creation payload."""
    return {
        "Name": name,
        "Color": "Blue",
        "SmtpApiActivated": True,
        "DeliveryType": resolve_postmark_delivery_type(env),
    }


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
    and no allow-list, fail closed.
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
    return _env_value(env, "OUTBOX_DRY_RUN").lower() in ("1", "true", "yes", "on")
