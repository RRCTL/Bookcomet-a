# Bookcomet-a public release checklist

[RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) is the public MVP. Follow these steps in order. Do not skip ahead to “Make public.”

## Step 1 — Merge and freeze

- [x] Treat Bookcomet-a as the public MVP. Keep the private `Bookcomet` repo private.
- [x] Record the freeze in [`CONTRIBUTING.md`](../CONTRIBUTING.md): no unrelated feature merges until after public + branch protection.
- [ ] Merge the public-MVP pull requests into `main` (this PR and, if still open, #1).
- [ ] Pause unrelated feature work until Step 6 is done.

## Step 2 — Source follow-up (this PR)

- [x] Cloud-AI notice on node-workspace Attach, Files node, and sidebar upload.
- [x] `.github/dependabot.yml` for pip, npm, and GitHub Actions.
- [x] Default CodeQL workflow for Python and JavaScript/TypeScript.
- [x] Fictional workflow diagram at `docs/assets/bookcomet-workflow.svg` (no real customer data).
- [x] README shows that diagram.
- [x] `SECURITY.md` keeps GitHub private vulnerability reporting (no separate security mailbox yet).

## Step 3 — Scan copies that are not in git

See the dated record in [`PUBLIC_COPY_SCAN.md`](PUBLIC_COPY_SCAN.md).

- [x] Agent scan: 0 releases, 0 tags, 0 LFS, 0 artifacts, wiki/pages/discussions off, no tracked secrets.
- [ ] Admin: open the two CI run logs and confirm they contain no real documents or keys.
- [ ] Admin: confirm org Packages and the enabled Projects board are empty of real documents.
- [ ] If any secret ever appeared, rotate it before going public.

## Step 4 — Clean-machine install (manual)

- [ ] Clone Bookcomet-a into a new empty directory.
- [ ] Follow README only (no private files or machine-local paths).
- [ ] Copy env examples and generate a new `JWT_SECRET_KEY`.
- [ ] `alembic upgrade head`, start API and UI.
- [ ] Confirm Settings → API and upload UI show the cloud-AI notice.
- [ ] Backend pytest and frontend lint / test / build.

## Step 5 — History rewrite only if Step 3 found a leak

- [ ] Skip if the scan stayed clean.
- [ ] If a secret was found: rotate, rewrite in a mirror clone, tell collaborators to re-clone, ask GitHub Support to purge caches.

## Step 6 — Make public, then re-apply controls the same day

GitHub drops push rulesets when visibility changes.

- [ ] Restrict who can change visibility; require 2FA for admins.
- [ ] Change visibility to public.
- [ ] Protect `main`: PR required, at least one review, required CI, no force-push.
- [ ] Enable secret scanning, generic secret detection, and push protection.
- [ ] Enable dependency graph, Dependabot alerts, and security updates (Dependabot config is already in-repo).
- [ ] Confirm CodeQL runs on `main` (workflow is already in-repo).
- [ ] Enable private vulnerability reporting so `SECURITY.md` has an inbox.
- [ ] Confirm Actions stay read-only for fork PRs.

## Step 7 — After public (do not block MVP)

- Upgrade or formally accept `cryptography` / `ecdsa` findings documented in CI.
- SBOM generation and signed releases.
- Stronger at-rest encryption for uploaded documents.
- A maintained fictional demo dataset.
