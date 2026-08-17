# Privacy

Bookcomet is self-hosted software. This document describes how a typical local MVP install handles data. It is not a substitute for your own privacy notice if you expose Bookcomet to other people.

## What Bookcomet stores locally

By default, a local install keeps the following on the machine that runs the backend:

| Data | Typical location | Purpose |
|---|---|---|
| Application database | SQLite file from `DATABASE_URL` (example: `backend/ai_accounting.db`) | Users, companies, transactions, journals, rules, jobs |
| Uploaded documents | `backend/uploads/` | Source PDFs, images, and CSVs for OCR/VLM and review |
| Derived OCR / VLM artifacts | Backend working directories and database fields | Page images, extracted rows, exceptions |
| Operator secrets | `backend/.env` | JWT secret, provider API keys, optional database password |
| Optional backups | Operator-chosen paths | Copies of the database and document library |

The frontend talks to your configured API. It does not ship a Bookcomet-hosted cloud database.

## Retention and deletion

- Removing a company, document, or journal from the UI deletes the corresponding application records when that action succeeds.
- Files already written under `backend/uploads/` and local SQLite files remain on disk until the operator deletes them.
- Stopping the process does not wipe data. Uninstalling means deleting the project directory, database file, uploads, logs, and `.env`.
- Optional SQLCipher (`DATABASE_PASSWORD`) encrypts the SQLite file at rest.
- Optional `UPLOADS_ENCRYPTION_KEY` wraps newly saved local upload files (prefix `BCENC1`). Existing plaintext files stay readable. OCR still decrypts in memory before a cloud provider call.

## Cloud vs local AI

Default VLM/LLM settings use an OpenAI-compatible gateway.

**Cloud mode:** when you configure a cloud OCR/LLM provider, uploaded document images, OCR content, and necessary company profile data are sent to that provider. The provider’s own retention, training, and subprocessors then apply.

**Local mode:** when `VLM_BASE_URL` / `LLM_BASE_URL` (or Settings → API) point at a local endpoint, that document and profile traffic stays on this device.

This notice also appears in Settings → API, company onboarding before Generate Company Profile, and the document upload surfaces.

Bookcomet does not claim to be fully offline. Optional cloud AI, dependency downloads, and operator-configured storage (for example S3-compatible object storage) can leave the machine.

## API keys and credentials

- Provider API keys and `JWT_SECRET_KEY` are stored in `backend/.env` or the operator’s secret store. They are not committed to git.
- Settings responses mask stored API keys.
- Rotate keys in the provider console if a machine, backup, or log may have leaked them.

## What this project does not do

- Bookcomet-a maintainers do not receive your receipts, bank statements, or company database when you clone or run the software.
- Telemetry that posted frontend debug events to localhost ingest endpoints is not part of this MVP.
- Sample CSVs and prompt fixtures in the repository are fictional. Do not replace them with real customer files.

## Operator responsibility

If you invite other users, open a tunnel, or deploy beyond loopback, you become the data controller for that instance. Add your own privacy notice, access control, backup policy, and provider-data-processing terms before collecting anyone else’s financial documents.
