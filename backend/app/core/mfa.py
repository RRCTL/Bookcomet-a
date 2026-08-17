"""SEC-CODE-009: optional TOTP MFA helpers (pyotp)."""
from __future__ import annotations

import pyotp


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str, issuer: str = "Bookcomet") -> str:
    totp = pyotp.TOTP(secret)
    label = (account_name or "user").strip() or "user"
    return totp.provisioning_uri(name=label, issuer_name=issuer)


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    cleaned = (code or "").strip().replace(" ", "")
    if not secret or not cleaned:
        return False
    totp = pyotp.TOTP(secret)
    try:
        return bool(totp.verify(cleaned, valid_window=valid_window))
    except Exception:
        return False
