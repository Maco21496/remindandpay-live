UPDATE account_sms_settings s
SET starter_credits_granted_at = COALESCE(s.terms_accepted_at, s.created_at, NOW())
WHERE s.starter_credits_granted_at IS NULL
  AND (
    s.terms_accepted_at IS NOT NULL
    OR EXISTS (
      SELECT 1
      FROM sms_credit_ledger l
      WHERE l.user_id = s.user_id
        AND l.reason = 'starter_pack'
      LIMIT 1
    )
  );
