# Bookcomet-a public release checklist

[RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a) is the public MVP. Follow these steps in order. Do not skip ahead to “Make public.”

## Step 1 — Merge and freeze

- [x] Treat Bookcomet-a as the public MVP. Keep the private `Bookcomet` repo private.
- [x] Record the freeze in [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- [ ] Merge the public-MVP pull requests into `main`.
- [ ] Pause unrelated feature work until Step 6 is done.

## Step 2 — Source follow-up

- [x] Cloud-AI notice on remaining upload paths.
- [x] Dependabot + CodeQL workflows.
- [x] Fictional workflow diagram in README.

## Step 3 — Scan copies that are not in git

See [`PUBLIC_COPY_SCAN.md`](PUBLIC_COPY_SCAN.md).

- [x] Agent scan: 0 releases, 0 tags, 0 LFS, 0 artifacts, no tracked secrets.
- [ ] Admin: open CI run logs and confirm they contain no real documents or keys.
- [ ] Admin: confirm org Packages and Projects have no real documents.

## Step 4 — Clean-machine install

See [`CLEAN_MACHINE_INSTALL.md`](CLEAN_MACHINE_INSTALL.md). Repeat with `scripts/clean_machine_verify.sh` in a fresh clone.

- [x] Agent: fresh directory, README-only install, new `JWT_SECRET_KEY`, `alembic upgrade head`.
- [x] Agent: backend pytest, frontend lint / test / build.
- [x] Agent: cloud-AI notice present in Settings and Processing source.
- [ ] Human: start API + UI on your machine and click through Settings → API and upload.

## Step 5 — History rewrite

See [`HISTORY_REWRITE.md`](HISTORY_REWRITE.md).

- [x] Skipped. Step 3 found no leak.

## Step 6 — Make public, then re-apply controls the same day

A PR cannot flip visibility. Follow [`GITHUB_PUBLIC_DAY.md`](GITHUB_PUBLIC_DAY.md).

- [ ] Restrict who can change visibility; require 2FA for admins.
- [ ] Change visibility to public.
- [ ] Protect `main` (PR, review, required CI, no force-push).
- [ ] Enable secret scanning, push protection, Dependabot, Code scanning, private vulnerability reporting.
- [ ] Confirm Actions stay read-only for fork PRs.

## Step 7 — After public (source work in this PR)

- [x] Formal accept record: [`DEPENDENCY_EXCEPTIONS.md`](DEPENDENCY_EXCEPTIONS.md) (`ecdsa` / `cryptography` pins).
- [x] SBOM workflow: `.github/workflows/sbom.yml` (CycloneDX artifacts).
- [x] Optional upload at-rest wrap: `UPLOADS_ENCRYPTION_KEY` (SEC-PUB-003).
- [x] Maintained fictional demo set: [`demo/`](demo/README.md).
- [ ] Signed GitHub Releases / attestations when the first versioned release exists.
