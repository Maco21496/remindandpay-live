# Billing & Trial Rollout Plan (Stripe Subscription + Access Restrictions)

## Current date baseline
- Draft created: **May 14, 2026 (UTC)**.
- Current agreed default trial: **30 days from sign-up date**.

## Scope summary
This plan covers introducing paid membership billing (Stripe recurring subscription) while keeping existing SMS credit top-ups separate.

- **Membership billing**: Stripe subscription (£29/month), Stripe invoices.
- **SMS wallet billing**: existing SMS credit ledger/top-ups (already live).
- **Restriction rule**: after trial expiry and without active membership, app cannot send outbound actions (email, SMS, and similar send operations).

---

## Business rules (locked)

1. **Trial duration policy**
   - Global default trial length is configurable in admin settings.
   - Initial default value: `30` days.
   - New users inherit this default at signup.

2. **Per-user trial override**
   - Each user gets their own assigned trial fields at signup.
   - Admin can override per-user trial later without changing global default.

3. **Trial anchor**
   - Trial starts from account sign-up timestamp.
   - Trial end is calculated once at signup (and can be manually adjusted per user).

4. **Restriction behavior after expiry**
   - If trial expired and no active subscription, block sending actions:
     - outbound emails,
     - outbound SMS,
     - reminder/chasing dispatches,
     - any queue enqueue that triggers outbound communication.
   - User is redirected/notified to open **Settings → Billing** to subscribe.

5. **Invoices source**
   - Use Stripe-hosted invoices (source of truth).
   - App Billing UI should show invoice list with view/download links.

---

## Data model additions

## 1) Global billing settings (admin-scoped)
Add table (or admin settings row) with at least:
- `default_trial_days` (int, default 30)
- `updated_at`
- `updated_by_user_id` (optional audit)

## 2) Per-account billing profile
Add table `account_billing_profile` (name can vary) with:
- `user_id` (unique FK users.id)
- `trial_days_assigned` (int)
- `trial_started_at` (datetime)
- `trial_ends_at` (datetime)
- `subscription_status` enum/string (`trialing`, `active`, `past_due`, `canceled`, `none`)
- `stripe_customer_id` (nullable)
- `stripe_subscription_id` (nullable)
- `created_at`, `updated_at`

## 3) Optional event/audit table
- `billing_event_log`
  - webhook event id/type,
  - user mapping,
  - status transitions,
  - processed timestamps,
  - error text for diagnostics.

---

## API & backend rollout phases

## Phase 0 — Planning & migrations (this document)
**Status:** ✅ In progress (planning locked).
- Define schema + migration sequence.
- Confirm business rules and enforcement points.

## Phase 1 — Billing settings + profile creation
**Status:** ⏳ Pending.
- Migration(s) for global billing config and per-user billing profile.
- On signup: create billing profile, assign global trial defaults, compute `trial_ends_at`.
- Admin API to update global `default_trial_days`.
- Admin API to override per-user trial fields.

## Phase 2 — Settings Billing tab UI shell
**Status:** ⏳ Pending.
- Add **Billing** tab under `/settings`.
- Display:
  - trial status/countdown,
  - membership status,
  - subscribe button placeholder,
  - invoice section placeholder.

## Phase 3 — Stripe subscription checkout
**Status:** ⏳ Pending.
- New endpoint: create Stripe Checkout Session in `mode=subscription` using £29 price.
- Metadata: `user_id`, `kind=membership_subscription`.
- Attach/prefill `customer_email` from authenticated user account.

## Phase 4 — Stripe webhooks for subscription lifecycle
**Status:** ⏳ Pending.
- Process at minimum:
  - `checkout.session.completed` (subscription mode)
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.paid`
  - `invoice.payment_failed`
- Update `account_billing_profile.subscription_status` accordingly.
- Idempotent processing by Stripe event ID.

## Phase 5 — Restriction enforcement
**Status:** ⏳ Pending.
- Add centralized gate checks before outbound actions.
- If blocked:
  - do not enqueue/send,
  - return clear reason,
  - generate app notification and CTA to Billing tab.

## Phase 6 — Trial-expiry notifications
**Status:** ⏳ Pending.
- Add pre-expiry app notifications (e.g., 7 days, 3 days, 1 day).
- Add expired notification and persistent banner.
- Reuse existing notification framework already in app.

## Phase 7 — Billing invoices in app
**Status:** ⏳ Pending.
- Endpoint to fetch Stripe invoices for account/customer.
- Billing tab table with:
  - invoice date,
  - amount,
  - status,
  - view/download links (Stripe hosted URL/PDF).

## Phase 8 — QA, rollout, and guardrails
**Status:** ⏳ Pending.
- Test matrix: trial active, trial expired, active subscription, past_due, canceled.
- Verify restriction points across email/SMS flows.
- Add operational runbook for support/admin overrides.

---

## Out-of-scope (for now)
- Migrating historical custom invoices into Stripe.
- Tax/VAT automation enhancements beyond Stripe defaults.
- Multi-plan pricing tiers beyond current £29 plan.

---

## Open decisions to confirm before implementation starts
1. Pre-expiry notification cadence default: `7/3/1` days?  
2. Grace period after failed subscription payment: none vs N days?  
3. Should users retain read-only dashboard access after expiry? (recommended: yes)

---

## Progress tracker
- [x] Planning document created.
- [ ] Migrations created.
- [ ] Billing tab added.
- [ ] Subscription checkout endpoint live.
- [ ] Subscription webhook handlers live.
- [ ] Restriction gate enabled.
- [ ] Invoices list/download in Billing tab.
- [ ] End-to-end test pass.
