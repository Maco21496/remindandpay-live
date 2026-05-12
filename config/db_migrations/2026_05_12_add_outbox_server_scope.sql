ALTER TABLE email_outbox
  ADD COLUMN server_scope ENUM('user_server','default_server') NOT NULL DEFAULT 'user_server' AFTER provider;

UPDATE email_outbox
SET server_scope = 'default_server'
WHERE template LIKE 'app_notification:%';

CREATE INDEX ix_email_outbox_user_scope_created
  ON email_outbox (user_id, server_scope, created_at);
