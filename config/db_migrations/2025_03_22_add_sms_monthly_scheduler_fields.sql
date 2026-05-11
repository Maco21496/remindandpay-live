ALTER TABLE account_sms_settings
  ADD COLUMN sms_enabled_at DATETIME NULL,
  ADD COLUMN next_number_charge_at DATETIME NULL,
  ADD COLUMN past_due_since DATETIME NULL;

CREATE UNIQUE INDEX uq_sms_credit_ledger_reference_id
  ON sms_credit_ledger (reference_id);

CREATE INDEX ix_account_sms_settings_due
  ON account_sms_settings (enabled, next_number_charge_at);
