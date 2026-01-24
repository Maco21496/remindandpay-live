ALTER TABLE account_sms_settings
    ADD COLUMN twilio_subaccount_sid VARCHAR(64) NULL,
    ADD COLUMN twilio_auth_token_enc VARCHAR(255) NULL,
    ADD COLUMN twilio_bundle_sid VARCHAR(64) NULL;
