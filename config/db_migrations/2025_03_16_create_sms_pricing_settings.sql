-- Create SMS pricing settings (single row)
CREATE TABLE IF NOT EXISTS sms_pricing_settings (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  sms_starting_credits INT NOT NULL DEFAULT 1000,
  sms_monthly_number_cost INT NOT NULL DEFAULT 100,
  sms_send_cost INT NOT NULL DEFAULT 5,
  sms_forward_cost INT NOT NULL DEFAULT 5,
  sms_suspend_after_days INT NOT NULL DEFAULT 14,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
