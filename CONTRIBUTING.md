# Contributing to Bookcomet

Thank you for helping improve Bookcomet. This repository, [RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a), is the public MVP.

Please also read [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), [`SECURITY.md`](SECURITY.md), and [`PRIVACY.md`](PRIVACY.md).

## What to work on

Useful contributions include documentation, tests, provider integrations, bank-specific prompt reliability, rule-memory templates, import/export paths, review UX, reconciliation safeguards, and security hardening.

Do not restore historical implementation notes, private client fixtures, or unpublished internal reports to the repository root.

## Development setup

Follow [`README.md`](README.md) and [`LOCAL_DEV_SETUP.md`](LOCAL_DEV_SETUP.md).

1. Copy `backend/config.env.example` to `backend/.env` and `frontend/.env.example` to `frontend/.env`.
2. Generate your own `JWT_SECRET_KEY`. Never commit a real `.env`.
3. Use only fictional company, vendor, customer, and bank-statement data.

## Branch and pull requests

1. Create a feature branch from `main`.
2. Keep the change focused. Prefer one concern per pull request.
3. Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md).
4. Expect review before merge. Do not force-push to `main`.

## Tests and style

Run the checks that apply to your change before opening a pull request:

```bash
# Backend
cd backend
pytest

# Frontend
cd frontend
npm run lint
npm run test:run
npm run build
```

On Windows PowerShell, activate the backend virtual environment first (`.\.venv\Scripts\Activate.ps1`).

Match the surrounding code style. Do not add drive-by refactors or new dependencies without a clear reason.

## Do not commit

The following must never enter git, pull requests, issues, screenshots, or CI artifacts:

- Real receipts, invoices, bank statements, customer or vendor lists, or “lightly redacted” versions of those files
- `.env`, API keys, JWT secrets, passwords, private keys, or tunnel credentials
- Local databases (`*.db`, `*.sqlite*`), uploads, logs, OCR dumps, or machine-local exports
- Unreferenced binaries such as `stripe.exe` or other vendor executables whose source is not documented

Use obviously fictional names such as `Example Trading Limited` and `Acme Supplies Ltd`. Masked account examples must stay in the documented placeholder shapes used by the privacy tests.

If you discover that real data or a secret was committed, rotate the credential first, then tell the maintainers privately using [`SECURITY.md`](SECURITY.md). Do not try to hide it in a later commit.

## Documentation

Durable documentation belongs in `docs/`, `backend/docs/`, or the relevant feature directory. Update [`docs/DOCUMENT_INVENTORY.md`](docs/DOCUMENT_INVENTORY.md) when you add a public-facing document.
