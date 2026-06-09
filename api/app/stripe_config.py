"""Helpers for resolving environment-specific Stripe configuration."""

import os
from collections.abc import Mapping

_STRIPE_ENVIRONMENT_PREFIXES = {
    "staging": "STRIPE_STAGING_",
    "live": "STRIPE_LIVE_",
}


def _stripe_environment(env: Mapping[str, str] | None = None) -> str:
    """Return the Stripe environment bucket for the current app environment."""
    env = env or os.environ
    app_env = str(env.get("APP_ENV") or "").strip().lower()
    environment = str(env.get("ENVIRONMENT") or "").strip().lower()
    if app_env == "staging" or environment == "staging":
        return "staging"
    return "live"


def get_stripe_env_var(name: str, default: str = "", env: Mapping[str, str] | None = None) -> str:
    """
    Resolve a Stripe environment variable with staging/live prefixes first.

    Staging deployments prefer STRIPE_STAGING_* values. All other deployments,
    including live/prod/production and unspecified environments, prefer
    STRIPE_LIVE_* values. The unprefixed STRIPE_* variable remains a fallback for
    backwards compatibility.
    """
    env = env or os.environ
    if not name.startswith("STRIPE_"):
        raise ValueError("Stripe variable names must start with STRIPE_")

    suffix = name.removeprefix("STRIPE_")
    prefix = _STRIPE_ENVIRONMENT_PREFIXES[_stripe_environment(env)]
    prefixed_value = str(env.get(f"{prefix}{suffix}") or "").strip()
    if prefixed_value:
        return prefixed_value
    return str(env.get(name) or default)


def get_stripe_secret_key() -> str:
    return get_stripe_env_var("STRIPE_SECRET_KEY", "")
