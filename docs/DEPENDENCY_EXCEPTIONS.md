# Dependency exceptions

CI runs `pip-audit` on `backend/requirements.txt` and ignores two findings until an upstream fix is available. This is the formal accept record for Step 7.

| ID | Package | Why it is ignored | Compensating control | Revisit |
|---|---|---|---|---|
| PYSEC-2026-1325 | `ecdsa` (via `python-jose`) | No fixed release at the time of this MVP | Bookcomet JWT uses **HS256 only**. Do not enable ECDSA/ES* algorithms. | When `python-jose` drops `ecdsa` or ships a fix |
| PYSEC-2026-3552 | `cryptography` PKCS7 decrypt | Needs `cryptography>=50`; `fastapi-mail` still requires `<50` | Local MVP does not use PKCS7 decrypt. Keep the pin in `requirements.txt`. | When `fastapi-mail` allows `cryptography>=50` |

Do not remove the `--ignore-vuln` flags in `.github/workflows/ci.yml` without updating this table.

npm `audit` findings on the frontend lockfile are tracked separately; they do not block the local MVP.
