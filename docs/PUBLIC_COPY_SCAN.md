# Bookcomet-a public-copy scan

Recorded 2026-08-17 against [RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) while the repository was still **private**. This is Step 3 of the public-MVP plan: copies that git cannot see.

No real company names, receipts, bank statements, or live secrets were found in the copies this agent can read. Items marked **manual confirm** need a GitHub admin click-through.

## Git refs (this clone)

| Check | Result |
|---|---|
| Branches | `main`, `cursor/public-mvp-governance-0b01`, this branch |
| Tags | none |
| Git LFS objects | none |
| Tracked `.exe` / `.pem` / `.p12` | none |
| Tracked `.env` files | none |
| History secret scan (`sk-live-`, `AKIA…`, private keys, `ghp_`) | no matches |

Bank-layout names such as HSBC/BOC/BEA remain in detection prompts on purpose. They are not sample customer data.

## GitHub copies

| Check | Result | Notes |
|---|---|---|
| Visibility | private | Do not flip until Steps 4–6 in [`PUBLIC_RELEASE_CHECKLIST.md`](PUBLIC_RELEASE_CHECKLIST.md) |
| Releases | 0 | `gh api repos/RRCTL/Bookcomet-a/releases` |
| Tags | 0 | |
| Wiki | disabled (`has_wiki: false`) | |
| GitHub Pages | `has_pages: false` | Pages API 403 for this token; flag says off |
| Discussions | disabled | |
| Projects | enabled | **manual confirm** the project board has no real documents |
| Packages / container images | not readable (404/400) | **manual confirm** in org Packages |
| Issues | Issues API 403 | `open_issues_count` is 1 and is pull request #1 |
| Pull requests | #1 only, 0 review comments | This PR adds Steps 1–3 |
| Actions runs | 2 successful CI runs | PR #1 and the `main` merge commit |
| Actions artifacts | `[]` on both runs | No downloadable artifacts |
| Default labels only | yes | no custom labels with private names |

## What this scan cannot prove

- Cached Actions logs still need a human open-and-read of the two CI runs.
- Organization-level Packages, Codespaces secrets, and environment secrets are outside this token.
- If a secret is later found, rotate it first, then follow GitHub’s sensitive-data removal guide.

CodeQL on this private repo needs `actions: read` so SARIF upload can call the workflow-run API. That permission is set in `.github/workflows/codeql.yml`.

## Next

Continue at Step 4 (clean-machine install) in [`PUBLIC_RELEASE_CHECKLIST.md`](PUBLIC_RELEASE_CHECKLIST.md). Do not make the repository public until that checklist is complete.
