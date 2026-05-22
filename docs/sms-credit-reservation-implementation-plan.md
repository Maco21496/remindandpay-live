# SMS Credit Reservation Implementation Plan (Final Policy)

Status: planning-only update for branch `fix/sms-credit-reservation`.

## 1) Final product policy (authoritative)

### No blocked/resume for SMS
- If an SMS cannot be sent due to insufficient credits, do **not** keep it in a resumable blocked queue.
- Cancel/skip current queued SMS rows.
- Future reminders must come from normal fresh enqueue (`/api/chasing_reminders/enqueue-due`) after top-up, if still eligible.

Rationale:
- Delayed resume risks stale content (invoice paid, stage changed, customer settings changed).

### Send-time sequence (immediately before Twilio)
1. Revalidate business state (`revalidate_chasing_sms_outbox`).
   - If invalid: no debit, no Twilio, mark `status="canceled"`, `last_error=<reason>`.
2. Check effective balance against pause threshold (`credit_send_pause_threshold`, default 100).
   - If below: cancel this row + sibling queued SMS rows in same scope; no debit; no Twilio; notify once.
3. Estimate exact SMS required credits from `EmailOutbox.body`.
   - `required_credits = estimated_segments * sms_send_cost`.
   - If insufficient: same cancel policy; no debit; no Twilio.
4. If valid and funded:
   - create pre-send debit deterministically,
   - call Twilio,
   - update outbox/provider ids,
   - webhook later reconciles metadata, no second debit.

---

## 2) Current branch baseline and what it enables

Already present:
- SMS segment estimator + tests (`sms_segments.py`).
- Reservation reference helpers + tests (`sms_credit_reservation.py`).
- Chasing SMS payload enrichment incl. eligibility context and `supersession_key`.
- `EmailOutbox.invoice_id` now set when determinable in chasing enqueue paths.
- Conservative `revalidate_chasing_sms_outbox(...)` helper + tests.
- Worker live behavior not yet changed.
- Webhook debit behavior not yet changed.

This is sufficient foundation to wire safe send-time cancellation-first behavior in worker.

---

## 3) Reference-id strategy decision (for retry safety)

### Decision: **B with deterministic attempt reference**
Use:
- Debit reference: `sms:outbox:<outbox_id>:attempt:<attempt_no>`
- Reversal reference: `sms:outbox:<outbox_id>:attempt:<attempt_no>:reversal`

Why this is safer than `sms:outbox:<id>`:
- If Twilio fails after pre-send debit and we reverse, a later retry needs a fresh debit.
- Single static reference blocks new debit creation or forces mutation-heavy logic.
- Attempt-scoped references preserve immutable ledger audit and safe retries.

Guardrails:
- At most one debit per outbox attempt reference.
- At most one reversal per attempt reversal reference.
- Insufficient-credit/stale cancellation path should **not** increment technical attempt count where avoidable.

Note:
- Attempt number must be determined before debit creation and remain stable within that attempt transaction.

---

## 4) Is `status="canceled"` safe?

Yes.
- Worker only claims `status == "queued"`, so canceled rows are ignored by sender loop.
- Existing outbox enum includes `canceled`.

Therefore, canceled is an appropriate terminal state for stale or insufficient-credit SMS.

---

## 5) Sibling cancellation strategy (insufficient-credit pause)

When balance is below pause threshold (or below exact required credits for current row):
- Cancel current SMS row with `last_error="insufficient_credits"` (or `credit_send_paused`).
- Cancel sibling **queued** SMS rows for same user and same chasing scope.

Recommended sibling selector (order of confidence):
1. Same `user_id`, `channel='sms'`, `status='queued'`, and same payload `eligibility_kind='chasing'`.
2. Same `rule_id` when present.
3. Same `supersession_key` prefix/group when present.
4. Optional narrow `next_attempt_at` time window to avoid canceling far-future rows.

If scope cannot be proven safely, prefer canceling only current row rather than over-canceling unrelated work.

---

## 6) Dedupe and future scheduler impact

- Canceled rows are not retried by worker.
- Chasing dedupe currently checks recent outbox creation by template/customer/channel/time window, not status.
- This can temporarily suppress immediate re-enqueue if a canceled row is still inside dedupe window.

Product-aligned effect:
- acceptable as natural pause behavior; fresh sends come from later scheduler cycles when still eligible.

Potential refinement (optional future):
- adjust dedupe to ignore canceled rows for enqueue logic, if product wants quicker post-top-up regeneration.

---

## 7) Where `credit_send_pause_threshold` should live

Recommendation: extend existing `sms_pricing_settings` + admin SMS pricing path (smallest/safest change).
- Add column `credit_send_pause_threshold INT NOT NULL DEFAULT 100`.
- Include in:
  - model (`SmsPricingSettings`),
  - admin pricing DTOs/routes,
  - pricing snapshot helpers.

Why:
- central SMS commercial controls already live there (`sms_send_cost`, monthly cost, etc.).
- avoids introducing new settings table for this incremental feature.

---

## 8) Notification recommendation

Use existing app notification framework enqueue path (`_enqueue_app_notification` pattern) with new template key:
- `sms_send_paused_insufficient_credits`.

Behavior:
- Notify once per user per cooldown/dedupe key during a pause event.
- Include current balance, threshold, and top-up guidance.

This can be wired after worker cancellation path lands, but in same feature tranche is reasonable.

---

## 9) Twilio webhook compatibility strategy

Transitional compatibility:
- New-model sends (pre-send debit exists with `sms:outbox...attempt...`) should cause webhook to update/reconcile existing ledger metadata only.
- Legacy behavior (`reference_id = MessageSid`) remains temporarily for rows sent before migration or without pre-send reservation marker.

Implementation principle:
- webhook checks for outbox-linked pre-send debit first (via outbox metadata/reference markers),
- falls back to legacy MessageSid debit creation only when no new-model debit exists.

---

## 10) Test plan (next implementation phase)

### Worker/business-state tests
1. Revalidation fail (`no_longer_overdue`) => row canceled, no Twilio, no debit.
2. Revalidation fail (`missing_context`) => row canceled, no Twilio, no debit.
3. Delivery mode no longer SMS => row canceled, no Twilio, no debit.

### Credit-pause tests
4. Balance below threshold => current row canceled + sibling queued SMS canceled; no debit; no Twilio.
5. Pause cancellation does not increment technical attempt_count (or only if explicitly intended and documented).

### Exact-cost tests
6. Balance above threshold but below `required_credits` => insufficient-credit cancellation path; no debit; no Twilio.

### Debit/reference/retry tests
7. Funded send creates one pre-send debit with attempt reference.
8. Twilio failure after debit creates one reversal for same attempt reference.
9. Retry attempt creates new attempt debit reference (no duplicate debit/reversal).

### Webhook compatibility tests
10. New-model webhook updates existing debit metadata (no second debit).
11. Legacy path still creates MessageSid debit where no pre-send debit exists.

### Regression tests
12. Worker still ignores canceled rows.
13. Scheduler can create fresh rows later if still eligible.

---

## 11) Exact next coding step

Implement **worker pre-Twilio gating only** (no webhook rewrite in same step):
1. In `outbox_worker` SMS branch, call `revalidate_chasing_sms_outbox` for chasing SMS payloads.
2. On invalid => mark canceled + reason; skip send.
3. Add pause-threshold + exact-cost checks (using existing balance helper + segment estimator).
4. On insufficient => cancel current + scoped siblings, emit one notification, skip send.
5. Add tests for these cancellation/no-Twilio/no-debit paths.

Then follow with pre-send debit + retry/reference mechanics in the next incremental step.
