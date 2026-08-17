# GitHub public-day runbook (Step 6)

A pull request cannot flip visibility or enable Security-tab products. An admin of [RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) must do this **on the same day** the repository becomes public. GitHub drops push rulesets when visibility changes.

Do this only after Steps 4 and 5 in [`PUBLIC_RELEASE_CHECKLIST.md`](PUBLIC_RELEASE_CHECKLIST.md).

The agent token cannot enable Security-tab products or change visibility (`403 Resource not accessible by integration`). An org admin must use the GitHub UI.

## Before you click Make public

1. Restrict who can change repository visibility (Settings → General → Danger zone / roles).
2. Require 2FA for organization admins.
3. Confirm [`PUBLIC_COPY_SCAN.md`](PUBLIC_COPY_SCAN.md) is still accurate.

## Make public

4. Settings → General → Danger zone → **Change repository visibility** → Public.

## Immediately after

5. Settings → Branches → add a `main` rule: require a pull request, at least one review, require the CI checks (Backend, Frontend, Repository Hygiene), dismiss stale reviews, **block force-push**.
6. Settings → Code security:
   - Secret scanning, generic secret detection, push protection
   - Dependency graph, Dependabot alerts, Dependabot security updates
   - **Code scanning** (so the in-repo CodeQL workflow can upload SARIF)
   - Private vulnerability reporting
7. Settings → Actions → General: fork pull requests get a **read-only** `GITHUB_TOKEN`. Workflows already set `permissions: contents: read` except CodeQL (`security-events: write`).
8. Confirm Dependabot opened no unexpected PRs that contain secrets.

## Already in the repository

| Control | Path |
|---|---|
| Dependabot config | `.github/dependabot.yml` |
| CodeQL workflow | `.github/workflows/codeql.yml` |
| SBOM workflow | `.github/workflows/sbom.yml` |
| Actions least privilege | `.github/workflows/ci.yml` |
| Private reporting instructions | `SECURITY.md` |
| Dependency accept record | [`DEPENDENCY_EXCEPTIONS.md`](DEPENDENCY_EXCEPTIONS.md) |

Signed release provenance (Sigstore / `gh attestation`) is deferred until there is a versioned GitHub Release. The SBOM workflow produces CycloneDX JSON artifacts on each `main` push.
