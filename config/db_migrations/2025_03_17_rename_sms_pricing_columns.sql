-- Rename SMS pricing columns for clarity
ALTER TABLE sms_pricing_settings
  CHANGE COLUMN starting_credits sms_starting_credits INT NOT NULL DEFAULT 1000,
  CHANGE COLUMN monthly_number_cost sms_monthly_number_cost INT NOT NULL DEFAULT 100,
  CHANGE COLUMN suspend_after_days sms_suspend_after_days INT NOT NULL DEFAULT 14;
