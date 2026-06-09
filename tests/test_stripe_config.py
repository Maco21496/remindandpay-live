from api.app.stripe_config import get_stripe_env_var


STRIPE_KEYS = {
    "STRIPE_SECRET_KEY": "SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY": "PUBLISHABLE_KEY",
    "STRIPE_WEBHOOK_SECRET": "WEBHOOK_SECRET",
    "STRIPE_STARTER_SUBSCRIPTION_PRICE_ID": "STARTER_SUBSCRIPTION_PRICE_ID",
    "STRIPE_SMS_TOPUP_10_PRICE_ID": "SMS_TOPUP_10_PRICE_ID",
    "STRIPE_SMS_TOPUP_25_PRICE_ID": "SMS_TOPUP_25_PRICE_ID",
    "STRIPE_SMS_TOPUP_50_PRICE_ID": "SMS_TOPUP_50_PRICE_ID",
    "STRIPE_SMS_TOPUP_100_PRICE_ID": "SMS_TOPUP_100_PRICE_ID",
}


def _env_for(app_env: str | None = None, environment: str | None = None) -> dict[str, str]:
    env = {}
    if app_env is not None:
        env["APP_ENV"] = app_env
    if environment is not None:
        env["ENVIRONMENT"] = environment
    for unprefixed, suffix in STRIPE_KEYS.items():
        env[unprefixed] = f"fallback_{suffix.lower()}"
        env[f"STRIPE_STAGING_{suffix}"] = f"staging_{suffix.lower()}"
        env[f"STRIPE_LIVE_{suffix}"] = f"live_{suffix.lower()}"
    return env


def test_staging_prefers_stripe_staging_variables_from_app_env():
    env = _env_for(app_env="staging", environment="production")

    for unprefixed, suffix in STRIPE_KEYS.items():
        assert get_stripe_env_var(unprefixed, env=env) == f"staging_{suffix.lower()}"


def test_staging_prefers_stripe_staging_variables_from_environment():
    env = _env_for(app_env="production", environment="staging")

    for unprefixed, suffix in STRIPE_KEYS.items():
        assert get_stripe_env_var(unprefixed, env=env) == f"staging_{suffix.lower()}"


def test_live_prefers_stripe_live_variables_for_live_prod_production_and_default():
    for app_env in ("live", "prod", "production", None):
        env = _env_for(app_env=app_env)
        for unprefixed, suffix in STRIPE_KEYS.items():
            assert get_stripe_env_var(unprefixed, env=env) == f"live_{suffix.lower()}"


def test_unprefixed_stripe_variables_are_backwards_compatible_fallbacks():
    env = _env_for(app_env="staging")
    for suffix in STRIPE_KEYS.values():
        env.pop(f"STRIPE_STAGING_{suffix}")
        env.pop(f"STRIPE_LIVE_{suffix}")

    for unprefixed, suffix in STRIPE_KEYS.items():
        assert get_stripe_env_var(unprefixed, env=env) == f"fallback_{suffix.lower()}"
