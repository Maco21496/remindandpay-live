CREATE TABLE IF NOT EXISTS billing_settings (
  id INT NOT NULL AUTO_INCREMENT,
  default_trial_days INT NOT NULL DEFAULT 30,
  updated_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_billing_settings_updated_by
    FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT chk_billing_settings_trial_days CHECK (default_trial_days >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account_billing_profiles (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  trial_days_assigned INT NOT NULL DEFAULT 30,
  trial_started_at DATETIME NOT NULL,
  trial_ends_at DATETIME NOT NULL,
  subscription_status VARCHAR(20) NOT NULL DEFAULT 'trialing',
  stripe_customer_id VARCHAR(64) NULL,
  stripe_subscription_id VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_account_billing_profiles_user_id (user_id),
  KEY ix_account_billing_profiles_status (subscription_status),
  CONSTRAINT fk_account_billing_profiles_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT chk_account_billing_profiles_trial_days CHECK (trial_days_assigned >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO billing_settings (default_trial_days)
SELECT 30
WHERE NOT EXISTS (SELECT 1 FROM billing_settings);
