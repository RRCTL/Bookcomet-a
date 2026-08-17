# Step 5 — History rewrite

**Skipped (reconfirmed 2026-08-17).** A full-ref scan (`scripts/github_history_scan.sh`, 91 commits, every remote branch tip) found:

- no tags
- no Git LFS
- no tracked `.env`, keys, `stripe.exe`, uploads, or `hsbc_debug`
- no live `sk_live_` / `AKIA` / `ghp_` / `github_pat_` / PEM private keys
- debug ingest hosts only in the CI blocklist

See [`GITHUB_PREFLIGHT.md`](GITHUB_PREFLIGHT.md) and [`PUBLIC_COPY_SCAN.md`](PUBLIC_COPY_SCAN.md).

Do not rewrite `main` unless a later scan finds a real secret or real financial document. If that happens: rotate the credential first, rewrite in a mirror clone, tell collaborators to re-clone, then ask GitHub Support to purge caches.
