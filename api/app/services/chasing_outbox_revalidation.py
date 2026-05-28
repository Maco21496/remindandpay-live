from dataclasses import dataclass
from typing import Optional, Any
import json


@dataclass(frozen=True)
class ChasingOutboxRevalidationResult:
    valid_to_send: bool
    reason: str
    details: dict


def _delivery_mode_allows_sms(db: Any, user_id: int) -> bool:
    row = db.execute(
        """
        SELECT enabled, chasing_delivery_mode
        FROM account_sms_settings
        WHERE user_id = :uid
        LIMIT 1
        """,
        {"uid": int(user_id)},
    ).first()
    if not row:
        return False
    enabled = bool(getattr(row, "enabled", row[0] if isinstance(row, tuple) else 0))
    mode = (getattr(row, "chasing_delivery_mode", row[1] if isinstance(row, tuple) and len(row) > 1 else "email") or "email").lower().strip()
    return enabled and mode in {"sms", "both"}


def _invoice_still_overdue(db: Any, *, user_id: int, customer_id: int, invoice_id: Optional[int]) -> bool:
    params = {"uid": int(user_id), "cid": int(customer_id)}
    invoice_filter = ""
    if invoice_id:
        invoice_filter = " AND i.id = :inv_id"
        params["inv_id"] = int(invoice_id)

    sql = f"""
        SELECT 1
          FROM invoices i
          LEFT JOIN (
                SELECT pa.invoice_id, COALESCE(SUM(pa.amount),0) AS alloc_sum
                  FROM payment_allocations pa
                  JOIN payments p ON p.id = pa.payment_id
                 WHERE p.customer_id = :cid
                 GROUP BY pa.invoice_id
          ) a ON a.invoice_id = i.id
         WHERE i.user_id = :uid
           AND i.customer_id = :cid
           AND i.kind = 'invoice'
           AND i.due_date IS NOT NULL
           AND i.due_date < CURRENT_DATE()
           AND (i.amount_due - COALESCE(a.alloc_sum,0)) > 0.005
           {invoice_filter}
         LIMIT 1
    """
    return db.execute(sql, params).first() is not None


def _customer_exists_for_user(db: Any, *, user_id: int, customer_id: int) -> bool:
    row = db.execute(
        """
        SELECT 1 FROM customers WHERE id = :cid AND user_id = :uid LIMIT 1
        """,
        {"cid": int(customer_id), "uid": int(user_id)},
    ).first()
    return row is not None


def _rule_enabled_for_user(db: Any, *, user_id: int, rule_id: int) -> bool:
    row = db.execute(
        """
        SELECT 1
        FROM reminder_rules
        WHERE id = :rid
          AND user_id = :uid
          AND reminder_type = 'chasing'
          AND reminder_enabled = 1
        LIMIT 1
        """,
        {"rid": int(rule_id), "uid": int(user_id)},
    ).first()
    return row is not None


def revalidate_chasing_sms_outbox(db: Any, outbox_row: Any) -> ChasingOutboxRevalidationResult:
    payload = outbox_row.payload_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return ChasingOutboxRevalidationResult(False, "missing_context", {"field": "payload_json"})
    payload = payload or {}

    if payload.get("eligibility_kind") != "chasing":
        return ChasingOutboxRevalidationResult(False, "missing_context", {"field": "eligibility_kind"})

    customer_id_value = payload.get("customer_id", getattr(outbox_row, "customer_id", None))
    rule_id_value = payload.get("rule_id", getattr(outbox_row, "rule_id", None))
    invoice_id_value = payload.get("invoice_id_at_render", getattr(outbox_row, "invoice_id", None))

    required = ["sequence_id", "step_id", "channel", "generated_at_utc", "supersession_key"]
    missing = [k for k in required if payload.get(k) in (None, "")]
    if customer_id_value in (None, ""):
        missing.append("customer_id")
    if rule_id_value in (None, ""):
        missing.append("rule_id")
    if missing:
        return ChasingOutboxRevalidationResult(False, "missing_context", {"missing": missing})

    if str(payload.get("channel") or "").lower() != "sms":
        return ChasingOutboxRevalidationResult(False, "missing_context", {"field": "channel"})

    customer_id = int(customer_id_value)
    if not _customer_exists_for_user(db, user_id=outbox_row.user_id, customer_id=customer_id):
        return ChasingOutboxRevalidationResult(False, "customer_not_found", {"customer_id": customer_id})

    if not _delivery_mode_allows_sms(db, outbox_row.user_id):
        return ChasingOutboxRevalidationResult(False, "delivery_mode_no_longer_sms", {"user_id": outbox_row.user_id})

    if not _rule_enabled_for_user(db, user_id=outbox_row.user_id, rule_id=int(rule_id_value)):
        return ChasingOutboxRevalidationResult(False, "rule_no_longer_applies", {"rule_id": rule_id_value})

    invoice_id = invoice_id_value
    if not _invoice_still_overdue(db, user_id=outbox_row.user_id, customer_id=customer_id, invoice_id=invoice_id):
        return ChasingOutboxRevalidationResult(False, "no_longer_overdue", {"invoice_id": invoice_id})

    return ChasingOutboxRevalidationResult(True, "valid", {"customer_id": customer_id, "invoice_id": invoice_id})
