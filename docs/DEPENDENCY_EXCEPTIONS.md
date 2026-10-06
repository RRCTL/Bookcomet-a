# Dependency exceptions

CI runs `pip-audit` on `backend/requirements.txt` and `backend/requirements-ocr.txt` with **no** `--ignore-vuln` flags.

| ID | Package | Status |
|---|---|---|
| CVE-2026-85394 | `python-jose` | Resolved: replaced with `PyJWT` (HS256 unchanged). |
| PYSEC-2026-1325 | `ecdsa` (via `python-jose`) | Resolved: no longer a transitive dependency. |
| PYSEC-2026-3552 | `cryptography` | Resolved: `cryptography>=50,<51` via `fastapi-mail>=1.6.8`. |
| PYSEC-2026-356 | `imgaug` | Resolved: removed from `requirements-ocr.txt` (unused). |

If a new finding must be temporarily accepted, document it in this table and add a matching `--ignore-vuln` in `.github/workflows/ci.yml` with a revisit condition.

npm `audit` findings on the frontend lockfile are tracked separately.
