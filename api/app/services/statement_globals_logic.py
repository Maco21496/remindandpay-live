# app/services/statement_globals_logic.py
from sqlalchemy import text
from sqlalchemy.orm import Session

def ensure_global_rules(db: Session, user_id: int) -> None:
    """
    Create the two global 'statements' rules (weekly + monthly) if missing.
    Matches the SQL you already had in the router.
    """
    # WEEKLY
    db.execute(text("""
        INSERT INTO reminder_rules
          (user_id, name, reminder_type, reminder_frequency,
           reminder_time, reminder_weekdays, reminder_month_days,
           reminder_enabled, is_global,
           schedule, escalate, created_at)
        SELECT :uid, 'Global weekly statements', 'statements', 'weekly',
               '14:00', 'mon', NULL,
               0, 1,
               '', 0, NOW()
        WHERE NOT EXISTS (
          SELECT 1 FROM reminder_rules
           WHERE user_id = :uid
             AND is_global = 1
             AND reminder_type = 'statements'
             AND reminder_frequency = 'weekly'
        )
    """), {"uid": user_id})

    # MONTHLY
    db.execute(text("""
        INSERT INTO reminder_rules
          (user_id, name, reminder_type, reminder_frequency,
           reminder_time, reminder_weekdays, reminder_month_days,
           reminder_enabled, is_global,
           schedule, escalate, created_at)
        SELECT :uid, 'Global monthly statements', 'statements', 'monthly',
               '14:00', NULL, JSON_ARRAY(1),
               0, 1,
               '', 0, NOW()
        WHERE NOT EXISTS (
          SELECT 1 FROM reminder_rules
           WHERE user_id = :uid
             AND is_global = 1
             AND reminder_type = 'statements'
             AND reminder_frequency = 'monthly'
        )
    """), {"uid": user_id})

    db.commit()
