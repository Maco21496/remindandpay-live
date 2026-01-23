CREATE TABLE sms_credit_ledger (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    entry_type ENUM('credit', 'debit') NOT NULL,
    amount INT NOT NULL,
    reason VARCHAR(120) NOT NULL,
    reference_id VARCHAR(64),
    metadata JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_sms_credit_ledger_user_id (user_id),
    CONSTRAINT fk_sms_credit_ledger_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
);
