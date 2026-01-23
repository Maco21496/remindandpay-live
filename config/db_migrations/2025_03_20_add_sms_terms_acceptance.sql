ALTER TABLE account_sms_settings
    ADD COLUMN terms_accepted_at DATETIME NULL,
    ADD COLUMN terms_version VARCHAR(32) NULL,
    ADD COLUMN terms_accepted_ip VARCHAR(64) NULL,
    ADD COLUMN accepted_pricing_snapshot JSON NULL;
