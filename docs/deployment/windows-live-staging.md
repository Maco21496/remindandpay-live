# Windows Live/Staging Deployment Notes

This is a repo-only deployment support document. It records the currently discovered Windows setup and the intended live/staging structure. It does not make server changes, service changes, database changes, or migrations.

## Hard rules for this branch

- No runtime code changes.
- No Windows service changes.
- No database migrations.
- Do not rename the current `invoice_chaser` database yet.
- Do not put real secrets in the repository.

## Current discovered live-ish setup

| Item | Current value |
| --- | --- |
| Web service | `InvoiceChaser` |
| Outbox worker service | `InvoiceChaserOutbox` |
| Scheduler service | `InvoiceChaserScheduler` |
| App port | `8055` |
| App bind | `127.0.0.1:8055` |
| Current live-ish folder | `C:\Users\Administrator\Documents\invoice_chaser_app\invoice_chaser` |
| Current config file | `C:\Users\Administrator\Documents\invoice_chaser_app\invoice_chaser\config\.env` |
| Current live database | `invoice_chaser` |

Current service shape discovered so far:

- `InvoiceChaser` runs uvicorn on `127.0.0.1:8055` from the current app folder.
- `InvoiceChaserOutbox` runs `python -m app.routers.outbox_worker` from the current `api` folder.
- `InvoiceChaserScheduler` runs `python -m app.routers.outbox_scheduler` from the current `api` folder.

## Current config loading behaviour

The app loads a folder-local config file from:

```python
Path(__file__).resolve().parents[2] / "config" / ".env"
```

That means each checkout can have its own uncommitted `config\.env` file.

The app currently uses `load_dotenv(..., override=True)`. Values in the folder-local `config\.env` override existing process or Machine environment variables for that app process. This is important because many provider credentials may exist as Machine environment variables today; staging must explicitly override or disable anything that should not inherit live values.

## Target folder structure

Keep the current live-ish folder in place until a separate, deliberate live migration is planned:

```text
C:\Users\Administrator\Documents\invoice_chaser_app\invoice_chaser
```

Target folders for the future structure:

```text
C:\Users\Administrator\Documents\remindandpay\
  live\
    api\
    web\
    config\.env
    .venv\
  staging\
    api\
    web\
    config\.env
    .venv\
  logs\
    live\
    staging\
```

## Live target

| Item | Target value |
| --- | --- |
| URL | `https://app.remindandpay.com` |
| Branch | `main` |
| Folder | `C:\Users\Administrator\Documents\remindandpay\live` |
| Config file | `C:\Users\Administrator\Documents\remindandpay\live\config\.env` |
| Port | `8055` |
| Database for now | `invoice_chaser` |
| Database later | `remindandpay_live` after a separate maintenance plan |

Do not rename `invoice_chaser` yet. The live database rename should be a separate maintenance task with backups, downtime planning, smoke tests, and rollback steps.

## Staging target

| Item | Target value |
| --- | --- |
| URL | `https://staging.remindandpay.com` |
| Branch | `staging` |
| Folder | `C:\Users\Administrator\Documents\remindandpay\staging` |
| Config file | `C:\Users\Administrator\Documents\remindandpay\staging\config\.env` |
| Port | `8056` |
| Database | `remindandpay_staging` |

Staging must explicitly override at least:

- `DB_URL` to point at `remindandpay_staging`.
- `APP_PORT=8056`.
- `APP_BASE_URL=https://staging.remindandpay.com`.
- `TWILIO_WEBHOOK_BASE_URL=https://staging.remindandpay.com`.
- Stripe test keys and test price IDs.
- Safe Postmark/Twilio values, or disable/whitelist real sends.
- Docmail test-mode values later, when Docmail is implemented.

See `config/.env.staging.example` for a placeholder template.

## Proposed staging NSSM services

Do not create or change services from this branch. This section is documentation for a later manual/server-side step.

The staging services should duplicate the current live service pattern, but point to the staging folder, staging venv, staging config, staging port, and staging logs.

Suggested names:

| Service | Purpose | Working directory | Arguments |
| --- | --- | --- | --- |
| `RemindAndPayStaging` | Web app | `C:\Users\Administrator\Documents\remindandpay\staging\api` | `-m uvicorn app.main:app --host 127.0.0.1 --port 8056` |
| `RemindAndPayStagingOutbox` | Outbox worker | `C:\Users\Administrator\Documents\remindandpay\staging\api` | `-m app.routers.outbox_worker` |
| `RemindAndPayStagingScheduler` | Scheduler | `C:\Users\Administrator\Documents\remindandpay\staging\api` | `-m app.routers.outbox_scheduler` |

Suggested separate staging logs:

```text
C:\Users\Administrator\Documents\remindandpay\logs\staging\web.log
C:\Users\Administrator\Documents\remindandpay\logs\staging\web.err.log
C:\Users\Administrator\Documents\remindandpay\logs\staging\worker.log
C:\Users\Administrator\Documents\remindandpay\logs\staging\worker.err.log
C:\Users\Administrator\Documents\remindandpay\logs\staging\scheduler.log
C:\Users\Administrator\Documents\remindandpay\logs\staging\scheduler.err.log
```

## Reverse proxy target

When staging is created, the reverse proxy should route:

- `app.remindandpay.com` to `http://127.0.0.1:8055`.
- `staging.remindandpay.com` to `http://127.0.0.1:8056`.

Verify the actual reverse proxy technology on the server before making changes, for example IIS ARR, nginx, Caddy, Apache, or a hosting control panel.

## Database notes

- Current live database remains `invoice_chaser` for now.
- Target staging database is `remindandpay_staging`.
- Do not point staging at `invoice_chaser`.
- Prefer schema-only plus minimal/sanitized seed data for staging.
- Do not copy production customer phone numbers, emails, provider tokens, outbox rows, or live payment identifiers into staging unless a separate sanitisation process is created and reviewed.
- Do not rename `invoice_chaser` to `remindandpay_live` until a separate maintenance plan is approved.

## Safety controls for staging

Before staging worker/scheduler processes are enabled, staging must be safe against accidental real sends and payments:

- Use Stripe test mode only (`sk_test_*`, test webhook secret, test price IDs).
- Set `TWILIO_WEBHOOK_BASE_URL=https://staging.remindandpay.com`.
- Use Twilio test credentials or a dedicated test/staging subaccount where possible.
- Keep SMS disabled by default or enforce recipient whitelisting before any staging SMS send.
- Use a Postmark sandbox/restricted server token where possible.
- Keep email disabled by default or enforce recipient/domain whitelisting before any staging email send.
- Use Docmail test mode later; do not put live Docmail credentials in staging.
- Add visible `STAGING` UI/runtime visibility in a later code branch before broader staging use.

## Repo files added for deployment support

- `docs/deployment/windows-live-staging.md` — this document.
- `config/.env.live.example` — placeholder live config template; no real secrets.
- `config/.env.staging.example` — placeholder staging config template; no real secrets.
- `scripts/deployment/README.md` — placeholder for future deployment helper scripts and rules.
