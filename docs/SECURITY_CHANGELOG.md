# Security change ledger

Stable IDs for security work. Prefer these over informal “phase” names in PRs and docs.

| ID | Area | Summary | Status |
|---|---|---|---|
| SEC-OPS-001 | Ops / docs | Local PC + tunnel setup checklist (`LOCAL_DEV_SETUP.md`, `config.env.example`) | On `main` |
| SEC-CI-001 | CI | Pytest/asyncio path fixes so backend CI is reliable | On `main` |
| SEC-CODE-001 | Code | No weak JWT default; strong secret; block insecure JWT when tunnel-like | On `main` |
| SEC-CODE-002 | Code | Require `REGISTER_INVITE_CODE` when exposure is tunnel-like | On `main` |
| SEC-CODE-003 | Code | Narrow CORS methods/headers; reject `*` origins | On `main` |
| SEC-CODE-004 | Code | Upload extension whitelist + magic-byte checks | On `main` |
| SEC-CODE-005 | Code | Sensitive log filter; quieter non-local default log level | On `main` |
| SEC-CODE-006 | CI | `pip-audit` on `backend/requirements.txt` in CI | On `main` |
| SEC-CODE-007 | Code | Optional SQLite at-rest encryption via SQLCipher (`DATABASE_PASSWORD`) | On `main` |
| SEC-OPS-002 | Ops / docs | OS secret store guidance + key rotation / session revoke notes | On `main` |
| SEC-OPS-003 | Ops / docs | Pure-local empty invite default; clearer where operators find/share the code | On `main` |
| SEC-CODE-008 | Code | Session revoke-all (`session_version` + `POST /auth/revoke-sessions`) | On `main` |
| SEC-CODE-009 | Code | Optional TOTP MFA (setup/enable/disable + login challenge) | On `main` |
| SEC-OPS-004 | Ops / docs | Phase 3 ongoing ops: rotation schedule, tunnel hygiene, quarterly re-audit | On `main` |
| SEC-PRIV-001 | Privacy | Fictional sample identifiers in UI/prompts; drop committed runtime/sample artifacts | On `main` |
| SEC-PRIV-002 | Privacy | Replace leftover real-shaped bank-prompt samples; CI-block OCR dumps | On `main` |
| SEC-PRIV-003 | Privacy | Replace leftover real filenames/payees in frontend tests with sample names | On `main` |
| SEC-PRIV-004 | Privacy | Replace remaining MDG/merchant fixtures with SAMPLE / SAMPLE-YYMM names | On `main` |
| SEC-PRIV-005 | Privacy | Remove private client denylist literals from SEC-PRIV-002 tests | This PR |
| SEC-PRIV-006 | Privacy | Remove frontend `#region agent log` / localhost ingest telemetry; CI block | This PR |
| SEC-PRIV-007 | Privacy | Replace HSBC/_shared real merchant prompt examples with SAMPLE-* tokens | This PR |
| SEC-PRIV-008 | Privacy | Cloud AI data-transmission notice in Settings API + onboarding + README | This PR |
| SEC-SUPPLY-001 | Supply chain | Remove unreferenced `backend/stripe.exe`; gitignore vendor CLI binaries | This PR |
| SEC-UX-001 | UX / security | Clarify MFA setup: paste Secret key only, not full otpauth URL | This PR |
| SEC-CODE-010 | Code | Session revoke on tenant deps + workflow WS (008 follow-on) | On `main` |
| SEC-CODE-011 | Code | Bind identity memberships to the active company; allowlist roles | On `main` |
| SEC-CODE-012 | Code | Harden `/settings/env` mask + lock process-wide keys | On `main` |
| SEC-CODE-013 | Code | Workflow WS auth without JWT in the query string | On `main` |
| SEC-PUB-001 | Public MVP | Community files, Bookcomet-a URLs, CI least privilege, upload-path cloud AI notice | This PR |
| SEC-PUB-002 | Public MVP | Freeze note, remaining upload notices, Dependabot, CodeQL, copy scan | This PR |
| SEC-PUB-003 | Public MVP | Optional `UPLOADS_ENCRYPTION_KEY` wrap for local upload files | This PR |
| SEC-PUB-004 | Public MVP | Clean-install record, dependency exceptions, SBOM workflow, demo dataset | This PR |
| SEC-PUB-005 | Public MVP | GitHub preflight: full-ref history + Actions/LFS/release scan; admin-only security/branch rows | This PR |

## Former phase map

| Informal phase | SEC-* IDs |
|---|---|
| Phase 0 | SEC-OPS-001, SEC-CI-001 |
| Phase 1 | SEC-CODE-001…006 |
| Phase 2 | SEC-CODE-007, SEC-OPS-002 (+ leftover SEC-CODE-008/009) |
| Phase 3 | SEC-OPS-004 |
| Phase 4 | SEC-CODE-010…013 |
| Privacy follow-on | SEC-PRIV-002…007 |
