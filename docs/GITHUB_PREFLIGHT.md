# GitHub preflight (ZIP cannot prove these)

Recorded **2026-08-17** against [RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) while the repository was still **private**. This is the Ask-mode follow-up to the ZIP public-readiness review: actions that a source ZIP cannot verify.

The agent token still cannot change visibility or Security-tab products (`403 Resource not accessible by integration`). An org admin must finish the **admin-only** rows before clicking **Make public**. Follow [`GITHUB_PUBLIC_DAY.md`](GITHUB_PUBLIC_DAY.md) the same day.

## 1. Git full-history scan — done (no rewrite)

| Check | Result |
|---|---|
| Remote heads scanned | `main` plus open `cursor/*` and Dependabot branches (91 commits across all refs; 81 on `main`) |
| Tags | none (`git ls-remote --tags` empty) |
| First `main` commit | `282a17f` 2026-08-17 — `Create README.md`; snapshot `d0e190d` the same day |
| Tracked `.env` / `.pem` / `.p12` / `.exe` / `stripe.exe` / `backend/uploads/` / `hsbc_debug` in any commit | none |
| Live secret patterns (`sk_live_`, `AKIA…`, `ghp_`, `github_pat_`, `-----BEGIN … PRIVATE KEY-----`) | no matches except this document naming the patterns |
| `127.0.0.1:7440` / `:7858` / `X-Debug-Session-Id` | only `.github/workflows/ci.yml` (blocklist). Not in `frontend/src` on any ref |

Re-run locally: `scripts/github_history_scan.sh`.

**History rewrite remains skipped.** Rotate first if a later scan finds a real secret, then follow [`HISTORY_REWRITE.md`](HISTORY_REWRITE.md).

## 2. Actions / artifacts / releases / LFS — agent-readable copies

| Check | Result | Notes |
|---|---|---|
| Releases | 0 | `gh api repos/RRCTL/Bookcomet-a/releases` |
| Tags | 0 | |
| Git LFS objects | none | `git lfs ls-files` empty; no LFS pointers in history |
| Wiki | disabled | `has_wiki: false` |
| GitHub Pages | off | `has_pages: false` (Pages API 403) |
| Discussions | disabled | |
| Environments | 0 | |
| Actions runs | 279 | CI, CodeQL, SBOM, Dependabot |
| Actions artifacts | 57, **all** named `bookcomet-a-sbom` | Sample zip is CycloneDX JSON (`backend.cdx.json`, `frontend.cdx.json`) — package names only, no customer files |
| Packages | not listable | org container 404; user npm `[]`. **Admin:** confirm org Packages UI |
| Projects | enabled | **Admin:** board has no real documents |
| Deploy keys / webhooks | 403 | **Admin:** confirm none point at private systems |

**Admin still required:** open a sample of Actions **logs** (not just artifacts). Logs become public with the repo. Confirm no pasted receipts, bank statements, or live keys.

## 3. GitHub Security settings — cannot enable from this token

| Setting | API this run | Admin action |
|---|---|---|
| Secret scanning + generic patterns + push protection | 403 | Enable on public day |
| Dependabot alerts / security updates | 403 | Enable (config file already in `.github/dependabot.yml`) |
| Code scanning | **not enabled** (explicit API message) | Enable so CodeQL can upload SARIF on `main` |
| Private vulnerability reporting | 404 | Enable; `SECURITY.md` already describes reporting |
| Visibility | **private** | Flip only after this page and [`GITHUB_PUBLIC_DAY.md`](GITHUB_PUBLIC_DAY.md) |

## 4. Branch governance — cannot read or write rules

| Setting | API this run | Admin action (same day as public) |
|---|---|---|
| Classic branch protection on `main` | 403 | Require PR, ≥1 review, required CI (Backend, Frontend, Repository Hygiene), dismiss stale reviews, **block force-push** |
| Repository rulesets | 403 (needs GitHub Pro **or** public) | Re-check after visibility change; GitHub drops push rulesets when going public |

Workflows already set `permissions: contents: read` except CodeQL (`security-events: write`).

## 5. What this preflight still cannot prove

- Human read-through of Actions log text
- Org Packages, Codespaces secrets, environment secrets (none configured here)
- That no collaborator laptop still holds a real `.env` or upload folder

Do not claim the repository is public-safe until an admin finishes sections 3–4 and the log/Packages click-through.
