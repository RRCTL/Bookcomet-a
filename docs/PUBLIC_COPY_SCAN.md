# Bookcomet-a public-copy scan

Recorded **2026-08-17** (updated same day after the ZIP public-readiness review) against [RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) while the repository was still **private**.

No real company names, receipts, bank statements, or live secrets were found in the copies this agent can read. Items marked **admin** need a GitHub owner click-through. The ZIP-only review cannot see git history or GitHub settings; that follow-up is [`GITHUB_PREFLIGHT.md`](GITHUB_PREFLIGHT.md).

## Git refs (all fetched remotes)

| Check | Result |
|---|---|
| Commits | 91 across all refs; 81 on `main` |
| Tags | none |
| Git LFS objects | none |
| Tracked `.exe` / `.pem` / `.p12` / `.env` | none in any commit |
| History secret scan (`sk_live_`, `AKIA…`, private keys, `ghp_`) | no matches |
| Debug ingest (`127.0.0.1:7440`, `:7858`, `X-Debug-Session-Id`) | CI blocklist only |

Bank-layout names such as HSBC/BOC/BEA remain in detection prompts on purpose. They are not sample customer data. Demo CSVs use `Example Trading Limited`, `Acme Supplies Ltd`, and `Sample Bank`.

Re-run: `git fetch --all --prune && scripts/github_history_scan.sh`.

## GitHub copies

| Check | Result | Notes |
|---|---|---|
| Visibility | private | Do not flip until [`GITHUB_PUBLIC_DAY.md`](GITHUB_PUBLIC_DAY.md) |
| Releases | 0 | |
| Wiki / Pages / Discussions | off | |
| Environments | 0 | |
| Projects | enabled | **admin:** no real documents on the board |
| Packages | not readable (404 / empty) | **admin:** org Packages UI |
| Actions artifacts | 57 × `bookcomet-a-sbom` only | CycloneDX JSON, package names |
| Actions runs | 279 | **admin:** sample logs for pasted documents or keys |
| Branch protection / rulesets | 403 | **admin:** set on public day |
| Code scanning | not enabled | **admin:** enable for SARIF upload |
| Secret scanning / Dependabot alerts | 403 | **admin:** enable on public day |

## What this scan cannot prove

- Cached Actions **log text** (artifacts are SBOM-only; logs are separate)
- Organization Packages, Codespaces secrets, and any secrets the token cannot list
- If a secret is later found, rotate it first, then follow GitHub’s sensitive-data removal guide

## Next

Finish admin rows in [`GITHUB_PREFLIGHT.md`](GITHUB_PREFLIGHT.md), then [`GITHUB_PUBLIC_DAY.md`](GITHUB_PUBLIC_DAY.md). Do not make the repository public until those are done.
