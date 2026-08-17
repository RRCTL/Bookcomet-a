# Bookcomet-a public release checklist

[RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) is the public MVP repository. Complete this list before changing GitHub visibility to public.

This checklist cannot be finished by a pull request alone. Items marked **manual** are GitHub or operator actions.

## Already handled in the source tree

- [x] Apache-2.0 `LICENSE`
- [x] `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `PRIVACY.md`, `NOTICE`
- [x] README clone and issue links point at Bookcomet-a
- [x] Cloud vs local AI notice in README, Settings → API, onboarding, and upload surfaces
- [x] Fictional sample CSVs (`Acme Supplies Ltd`, `Example Trading Limited`)
- [x] `backend/stripe.exe` is not present and is gitignored
- [x] CI default `permissions: contents: read`
- [x] CI blocks committed `.env` / databases / debug telemetry markers
- [x] Short git history (MVP snapshot) with no tags, releases, or Git LFS objects

## Manual — scan copies that git cannot see

- [ ] Review GitHub Actions logs, caches, and artifacts on this repository
- [ ] Confirm there are no Releases, Packages, container images, Pages, Wiki pages, or LFS objects with real documents
- [ ] Search Issues, Discussions, Projects, and PR comments for real company names, receipts, or secrets
- [ ] If any secret ever appeared, rotate it before going public

## Manual — GitHub settings on the day you go public

GitHub disables push rulesets when a repository becomes public. Re-check controls immediately after the visibility change.

- [ ] Restrict who can change repository visibility; require 2FA for admins
- [ ] Protect `main`: pull request, at least one review, required CI, no force-push
- [ ] Enable Secret scanning, generic secret detection, and push protection
- [ ] Enable Dependency graph, Dependabot alerts, and Dependabot security updates
- [ ] Enable default CodeQL for Python and JavaScript/TypeScript
- [ ] Enable private vulnerability reporting (so `SECURITY.md` has a working inbox)
- [ ] Confirm Actions workflow permissions stay read-only for pull requests from forks

## Manual — clean-machine install

- [ ] Clone Bookcomet-a into a new empty directory
- [ ] Follow README only (no private files or machine-local paths)
- [ ] `alembic upgrade head`, backend tests, frontend lint/test/build
- [ ] Confirm Settings → API and upload UI show the cloud AI notice

## After the first public release

These should not block the local MVP, but they belong on the roadmap:

- Upgrade or formally accept `cryptography` / `ecdsa` findings documented in CI
- SBOM generation and signed releases
- Stronger at-rest encryption for uploaded documents
- A maintained fictional demo dataset
