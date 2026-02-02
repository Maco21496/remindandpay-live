# Monthly Number Scheduler Plan

## Goal
Implement monthly number billing that is:

- Predictable per user (same day each month as activation).
- Ledger-based (entries appear in `SmsCreditLedger`).
- Automated via scheduler (runs daily).
- Safe against double-charges (idempotent reference IDs).

This plan assumes the current branch already creates starter credits and the initial number fee ledger entries on enable. That logic remains the activation anchor point.

## Proposed Schema Changes
Add these fields to `account_sms_settings`:

| Column | Type | Purpose |
| --- | --- | --- |
| `sms_enabled_at` | DATETIME | Activation timestamp (anchor for monthly schedule). |
| `next_number_charge_at` | DATETIME | Next time to charge monthly number fee. |
| `past_due_since` | DATETIME | When balance first fell short for monthly fee (optional). |

### Idempotency Guard (required)
Add a unique index on `sms_credit_ledger.reference_id` so repeated scheduler runs do not double-charge. Monthly number charge entries must always set a unique `reference_id`.

Example:
- `reference_id = "sms_number_monthly:settings:{settings_id}:{YYYY-MM}"`

Proposed indices:

```sql
CREATE UNIQUE INDEX uq_sms_ledger_reference_id
  ON sms_credit_ledger (reference_id);

CREATE INDEX ix_sms_settings_due
  ON account_sms_settings (enabled, next_number_charge_at);
```

> MySQL allows multiple `NULL` values in a unique index, so this is safe as long as monthly charges always set `reference_id`.

## Activation Flow (existing branch)
On first enablement, the current flow:

- sets terms accepted
- provisions Twilio subaccount/number
- writes starter credits + initial number fee into the ledger

New branch change: also set:

- `sms_enabled_at = now`
- `next_number_charge_at = now + 1 month`
- `past_due_since = null`

## Scheduler Logic (new branch)
A daily scheduler task should:

### Step 1 — Query users due for monthly charge
```sql
SELECT *
FROM account_sms_settings
WHERE enabled = 1
  AND twilio_phone_number IS NOT NULL
  AND next_number_charge_at IS NOT NULL
  AND next_number_charge_at <= NOW();
```

### Step 2 — Check ledger balance
Reuse the existing ledger balance helper so UI and scheduler stay consistent.

### Step 3 — If balance >= monthly fee
Create a ledger debit:

- `reason = "sms_number_monthly"`
- `amount = pricing.sms_monthly_number_cost`
- `reference_id = "sms_number_monthly:settings:{settings_id}:{YYYY-MM}"`
- `metadata = {"source": "sms_number_scheduler", "cycle": "YYYY-MM"}`

Then:

- `next_number_charge_at = next_number_charge_at + 1 month`
- `past_due_since = null`

### Step 4 — If balance < monthly fee
Do not charge. Instead:

- if `past_due_since` is `null`, set it to `now` and send the warning once
- use the existing “suspend after (days)” setting as the grace period
- after `past_due_since + suspend_after_days`, release the number (later branch)

## “Same Day Each Month” Edge Cases
If enabled on the 29th/30th/31st, decide how to handle months without that day.

Recommended policy: charge on the last day of the month when the target day doesn’t exist. Implement this intentionally instead of relying on library defaults.

## Grace Period & Suspension (later branch)
After grace period expires:

- release the number via Twilio API
- disable SMS or clear number fields in settings

This should be a separate branch to keep scheduler/ledger changes isolated.

## Ledger Entry Format
When charging monthly fee:

```python
SmsCreditLedger(
    user_id=user_id,
    entry_type="debit",
    amount=monthly_cost,
    reason="sms_number_monthly",
    reference_id=f"sms_number_monthly:settings:{settings_id}:{yyyy_mm}",
    metadata={
        "source": "sms_number_scheduler",
        "cycle": "2026-02",
    },
)
```

This mirrors the existing ledger usage for starter credits and SMS send debits, and uses `metadata` (the existing JSON column).

## UI / Admin Visibility (optional in this branch)
- Display `past_due_since` or computed status next to SMS number on admin dashboard.
- Show `next_number_charge_at` on SMS billing page.

## Why this branch is safe
- Ledger stays authoritative — no hidden balance fields.
- Monthly fees are explicit and auditable.
- Scheduler logic can be tested independently.
- Idempotency prevents double-charging on retries.

## Summary of Work
- Schema additions to `account_sms_settings`.
- Activation sets `sms_enabled_at` + `next_number_charge_at`.
- Scheduler job charges monthly fee + ledger entry with unique `reference_id`.
- Grace/suspension handled in a follow-up branch.
- Optional UI for status visibility.
