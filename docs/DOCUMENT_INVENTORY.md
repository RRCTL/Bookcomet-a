# Documentation Inventory

Public-facing docs kept in this repository after cleanup. Prefer updating these over adding new root-level notes.

## Onboarding

| Path | Purpose |
|------|---------|
| `README.md` | Product overview, stack, quick start for the Bookcomet-a public MVP |
| `LOCAL_DEV_SETUP.md` | Local setup, SEC-OPS-001…004 checklists, SEC-CODE boot gates / SQLCipher / MFA / sessions, tunnel, smoke checks |
| `LICENSE` | Apache License 2.0 |
| `NOTICE` | Apache attribution and third-party / non-bundled binary notes |
| `SECURITY.md` | Private vulnerability reporting |
| `CONTRIBUTING.md` | Branch, test, and no-real-data rules |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1 |
| `PRIVACY.md` | Local storage, cloud AI data flow, retention, and deletion |
| `frontend/README.md` | Frontend package notes |
| `bin/README.md` | Helper scripts |

## Backend / product docs

| Path | Purpose |
|------|---------|
| `backend/PROCESSING_FLOW.md` | Document processing flow |
| `backend/docs/OCR_PROVIDERS_GUIDE.md` | OCR provider configuration |
| `backend/docs/AI_ML_CAPABILITIES.md` | AI/OCR capability overview |
| `backend/app/agent/bookcomet_skills/*/SKILL.md` | Agent skill definitions (ap, ar, bank, other, recon, report) |

## Meta

| Path | Purpose |
|------|---------|
| `docs/DOCUMENT_INVENTORY.md` | This inventory |
| `docs/SECURITY_CHANGELOG.md` | Security change IDs (SEC-OPS / SEC-CODE / SEC-CI) |
| `docs/PUBLIC_RELEASE_CHECKLIST.md` | Step-by-step public-MVP plan and remaining operator gates |
| `docs/PUBLIC_COPY_SCAN.md` | Dated scan of GitHub copies that are not in git |
| `docs/CLEAN_MACHINE_INSTALL.md` | Step 4 README-only install evidence |
| `docs/HISTORY_REWRITE.md` | Step 5 skip record |
| `docs/GITHUB_PUBLIC_DAY.md` | Step 6 admin runbook |
| `docs/DEPENDENCY_EXCEPTIONS.md` | Accepted pip-audit ignores |
| `docs/demo/README.md` | Fictional demo dataset |
| `.github/workflows/sbom.yml` | CycloneDX SBOM on CI |
| `scripts/clean_machine_verify.sh` | README-only install check for Step 4 |
| `docs/assets/bookcomet-workflow.svg` | Fictional README workflow diagram |
| `.github/pull_request_template.md` | PR template |
| `.github/dependabot.yml` | Weekly pip / npm / Actions updates |
| `.github/workflows/codeql.yml` | CodeQL for Python and JavaScript/TypeScript |

## Intentionally not published at root

Historical migration guides, ComfyUI reference notes, bank VLM specs, categorization workflow notes, and Chinese-language implementation reports were removed from the public tree. Local reference trees (`refer/`, `bookcomet-oss-preview/`) remain on disk but are gitignored.

## Do not commit

- Real `.env` files
- Local databases (e.g. `backend/ai_accounting.db`)
- Uploads, logs, caches, and `tunnel-credentials.json`
- OCR/runtime dumps under `backend/transactions/` (CI hygiene rejects these if tracked)
- Sample statements or extracted JSON that contain real-world customer data (`backend/docs/*.pdf.json`)
