# Docmail Printed Postal Module — Design/Planning Pass

## Scope of this pass
- Documentation and architecture planning only.
- No production Docmail send implementation.

## 1) Existing credit patterns in app (inspection summary)
Current ledger model and semantics:
- Ledger table is `sms_credit_ledger` with:
  - `entry_type` (`credit`/`debit`)
  - `amount`
  - `reason`
  - unique `reference_id` (nullable; unique index)
  - metadata JSON (`details`).
- Balance helper:
  - `_calculate_credit_balance(db, row)` sums ledger credits-debits.
  - `_effective_credit_balance(db, row)` wraps balance behavior used in runtime decisions.
- Existing reason usage:
  - debit `sms_send` (reference = Twilio `MessageSid`)
  - debit `sms_number_monthly` (deterministic cycle ref)
  - credit `stripe_topup` / `billing_transaction_topup`
  - debit `stripe_refund_reversal` / `billing_transaction_refund_reversal`
- Idempotency pattern:
  - deterministic `reference_id` + DB unique index + integrity-error-safe retries.
  - webhook handlers and scheduler both check for prior row or absorb duplicate insert via uniqueness.
- Admin pricing currently centralized in `sms_pricing_settings` and read via `_ensure_pricing`.

How postal should plug in:
- Reuse the same ledger table/reconciliation semantics now.
- Add new reasons:
  - `postal_send` (debit)
  - `postal_send_reversal` (credit or compensating debit strategy, see below).
- Deterministic reference IDs per postal job submission.
- Extend ledger activity mapping so postal entries render clear UI labels.

## 2) Proposed postal_jobs data model
Proposed new table: `postal_jobs`.

Columns (first safe version):
- `id` (PK)
- `user_id` (FK users)
- `customer_id` (FK customers, nullable)
- `source_type` (e.g. `invoice`, `reminder`, `statement`, `ad_hoc`)
- `source_id` (nullable)
- `document_kind` (`posted_reminder`, `statement_letter`, `pre_solicitor_letter`, ...)
- `status` (enum/string lifecycle)
- `credits_cost` (int, required)
- `ledger_reference_id` (varchar64, unique nullable)
- `provider_name` (`docmail`)
- `provider_mailing_guid` (nullable)
- `provider_document_guid` (nullable)
- `provider_mailing_list_guid` (nullable)
- `provider_status` (nullable)
- recipient snapshot fields:
  - `recipient_name`
  - `address_line1..line6`
  - `postcode`
  - `country`
- `document_file_ref` (internal blob/path key)
- `idempotency_key` (unique per user-action scope)
- `request_metadata` (JSON)
- `response_metadata` (JSON)
- timestamps:
  - `created_at`, `updated_at`
  - `charged_at`, `submitted_at`, `proof_ready_at`, `confirmed_at`, `sent_at`, `failed_at`, `reversed_at`, `cancelled_at`

Indexes:
- `(user_id, created_at desc)`
- unique `(ledger_reference_id)`
- unique `(user_id, idempotency_key)`
- `(provider_name, provider_mailing_guid)`

## 3) Status lifecycle (refined for Docmail proof/approval model)
Recommended statuses:
- `draft`
- `pending_submission`
- `blocked_insufficient_credits`
- `charged`
- `submitted_to_docmail`
- `processing`
- `proof_ready`
- `awaiting_approval` (if UserApprove flow applies)
- `confirmed_for_send`
- `sent`
- `failed_retryable`
- `failed_terminal`
- `reversed`
- `cancelled`

Rationale:
- Docmail explicitly supports proof generation and optional user approval flow, so separating `proof_ready` / `awaiting_approval` / `confirmed_for_send` prevents ambiguity.

## 4) Credit charging design
Recommendation: **A (debit before provider submit), with deterministic reversal policy.**

Why A is safer here:
- Avoids provider send without internal user charge.
- DB uniqueness on ledger `reference_id` supports exactly-once debit semantics.
- Docmail flow can include asynchronous processing and approval states; debiting before submission simplifies ownership of risk.

Safe sequence:
1. Create/find `postal_jobs` row in `draft`/`pending_submission` with deterministic `idempotency_key`.
2. Determine `credits_cost` from pricing snapshot.
3. Compute current balance using existing helper.
4. If insufficient, set `blocked_insufficient_credits`; stop before provider call.
5. Insert debit ledger row:
   - `reason = postal_send`
   - `reference_id = postal:job:<postal_job_id>`
   - `details.document_kind`, `details.postal_job_id`.
6. On uniqueness conflict, treat as already charged and continue idempotently.
7. Call Docmail create/add/process-to-proof flow.
8. If provider submission fails before a sendable state, set job failed and create compensating reversal once:
   - `reason = postal_send_reversal`
   - `reference_id = postal:job:<id>:reversal` (also unique).
9. Retries:
   - Never create second debit.
   - Only retry provider submission from existing charged job.
   - Never create second reversal if already reversed.

Open decision:
- reversal entry type should likely be `credit` (more intuitive for compensation), while retaining explicit reason `postal_send_reversal`.

## 5) Pricing design
Smallest safe first implementation:
1. Add postal fields to existing `sms_pricing_settings` **temporarily**:
   - `postal_standard_letter_cost`
   - `postal_pre_solicitor_letter_cost` (default 3500)
   - `postal_statement_letter_cost`
2. Extend existing admin pricing route/UI payload to include these fields.
3. Centralize cost lookup helper:
   - `get_credit_cost(document_kind)` reading from DB settings.
4. For medium-term cleanup, introduce `credit_pricing_settings`/`credit_price_rules` and migrate SMS+postal there.

Reasoning:
- Minimal schema and route churn for first release.
- No hardcoding: all postal costs sourced from settings row + per-job snapshot persisted into `postal_jobs.credits_cost`.

## 6) Proposed API/routes/UI (design only)
User routes:
- `POST /api/postal/jobs` — create draft postal job + cost quote snapshot.
- `POST /api/postal/jobs/{id}/preview` — submit to Docmail proof flow / refresh proof.
- `POST /api/postal/jobs/{id}/confirm` — approve/send (maps to process/approve calls as needed).
- `GET /api/postal/jobs/{id}` — job detail + provider status/proof info.
- `GET /api/postal/jobs` — paginated list.

Admin routes:
- `GET /api/admin/postal/jobs`
- `POST /api/admin/postal/jobs/{id}/retry`
- `POST /api/admin/postal/jobs/{id}/cancel`
- `POST /api/admin/postal/jobs/{id}/reverse`
- pricing via existing admin pricing endpoint extension (near-term).

UI integration:
- Add “Send by post” choice where reminder/statement delivery action exists.
- Before confirm, show credit cost and resulting projected balance.
- Postal history/status panel in customer/reminder timeline and/or account communications.
- Ledger display mapping:
  - `postal_send` => “Printed letter” / “Pre-solicitor letter” by `document_kind`
  - `postal_send_reversal` => “Postal send reversal”.

## 7) Proposed service/client structure
- `api/app/services/postal/docmail_client.py`
  - thin SOAP wrapper for operations used in first release.
- `api/app/services/postal/postal_service.py`
  - orchestration (job creation, charging, provider submission, retries, reversal policy).
- `api/app/services/postal/postal_pricing.py`
  - central pricing lookup and document-kind mapping.
- `api/app/routers/postal.py`
  - user routes.
- `api/app/routers/admin_postal.py`
  - admin operational routes.

Client behavior:
- Authenticate once per workflow using `GetUserLoginKey` and pass key reuse.
- Persist provider GUIDs + sanitized request/response metadata.
- Support proof-first path and polling/backoff (`GetStatus`).

## 8) Test plan (first tranche; no live providers)
Target: `python -m pytest -q` remains green with fakes/mocks.

Proposed tests:
1. Route registration for new postal routes.
2. Insufficient credits blocks submission and provider client not invoked.
3. Sufficient credits => exactly one `postal_jobs` row + one `sms_credit_ledger` debit (`postal_send`).
4. Retry same job/idempotency key does not duplicate debit.
5. Simulated provider failure after debit transitions to failed + single reversal.
6. Ledger activity mapping:
   - Movement `Debit` for `postal_send`
   - description from `document_kind`
   - reference = `postal:job:<id>`
   - units = 1 (postal piece)
7. Fake Docmail client contract tests:
   - create/process/proof/status success path
   - deterministic failure injection.

## 9) Risks and open questions
- Some key constraints (address validation granularity, full status enum definitions, precise cancellation/refund windows) are not fully explicit in extracted text snippets and may need PDF/WSDL/vendor confirmation.
- Proof/approval branch behavior depends on account-level settings; must account for both required and non-required approval flows.
- Concurrency: simultaneous retries/submissions must lock per postal job + rely on unique ledger refs.
- Reversal policy boundary: define exact provider state threshold beyond which reversal is disallowed (e.g., confirmed/printed pipeline).

## 10) Suggested smallest first coding step (next pass)
1. Add migration for `postal_jobs` table only.
2. Add postal pricing fields to existing `sms_pricing_settings` (temporary approach).
3. Add pure-Python domain/service skeleton + fake Docmail client interface (no real SOAP call).
4. Add route registration stubs returning `501`/feature-flagged placeholders.
5. Add tests for route registration + idempotent charge logic with mocked provider.

