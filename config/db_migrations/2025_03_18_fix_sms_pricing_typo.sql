-- Fix SMS pricing column typo (montly -> monthly)
ALTER TABLE sms_pricing_settings
  CHANGE COLUMN sms_montly_number_cost sms_monthly_number_cost INT NOT NULL DEFAULT 100;
