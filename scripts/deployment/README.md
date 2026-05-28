# Deployment Scripts Placeholder

This folder is reserved for future repo-owned deployment helper scripts.

## Current scope

This branch intentionally adds documentation and example environment files only.

Do not use this folder to make server changes in this branch:

- Do not create or edit NSSM services.
- Do not restart services.
- Do not apply database migrations.
- Do not rename the `invoice_chaser` database.
- Do not add real secrets.

## Current discovered Windows services

The current live-ish server setup is documented in `docs/deployment/windows-live-staging.md`:

- `InvoiceChaser`
- `InvoiceChaserOutbox`
- `InvoiceChaserScheduler`

The current app port is `8055`, and the current live-ish folder is:

```text
C:\Users\Administrator\Documents\invoice_chaser_app\invoice_chaser
```

## Future script rules

If deployment helper scripts are added later, they should:

1. Be safe to read without credentials.
2. Require explicit operator confirmation for destructive actions.
3. Print the target environment before doing anything.
4. Keep live and staging service names, ports, folders, config files, and logs separate.
5. Never embed real secrets.
6. Be tested on staging before live.

Potential future scripts:

- Export current NSSM service configuration to a text report.
- Validate that a folder-local `config\.env` has required keys without printing secret values.
- Check whether staging points at `remindandpay_staging` and port `8056`.
- Tail environment-specific log files.
