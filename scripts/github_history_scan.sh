#!/usr/bin/env bash
# Scan every local git ref for filenames and blob patterns a public ZIP cannot see.
# Usage: from the repository root, after `git fetch --all --prune`.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

echo "refs: $(git rev-list --all | wc -l) commits"
echo "tags: $(git ls-remote --tags origin 2>/dev/null | wc -l)"

echo
echo "== added paths that look like secrets or runtime data =="
git log --all --pretty=format: --name-only --diff-filter=A | sort -u \
  | grep -Ei '\.(env|pem|p12|pfx|jks)$|id_rsa|stripe\.exe|\.sqlite3?$|(^|/)backend/uploads/|(^|/)backend/hsbc_debug/' \
  && exit 1 || echo "(none)"

echo
echo "== live secret / private-key patterns on every ref tip =="
if git grep -I -n -E 'sk_live_[0-9A-Za-z]{10,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN ([A-Z]+ )?PRIVATE KEY-----' $(git rev-parse --all) \
  | grep -v 'docs/PUBLIC_COPY_SCAN.md' \
  | grep -v 'docs/GITHUB_PREFLIGHT.md' \
  | grep -v 'scripts/github_history_scan.sh'; then
  echo "possible secret match" >&2
  exit 1
fi
echo "(none outside scan docs)"

echo
echo "== debug ingest markers outside CI blocklist =="
if git grep -I -n -E '127\.0\.0\.1:(7440|7858)|X-Debug-Session-Id' $(git rev-parse --all) \
  | grep -v '.github/workflows/ci.yml' \
  | grep -v 'docs/PUBLIC_COPY_SCAN.md' \
  | grep -v 'docs/GITHUB_PREFLIGHT.md' \
  | grep -v 'scripts/github_history_scan.sh'; then
  echo "debug ingest marker outside allowlist" >&2
  exit 1
fi
echo "(CI blocklist only)"

echo
echo "github_history_scan: ok"
