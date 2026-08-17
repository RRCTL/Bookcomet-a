import re

_MIN_LEN = 8
_MAX_LEN = 256


def validate_password_strength(password: str) -> str:
    """Raise ValueError with a stable message if password does not meet policy."""
    if not isinstance(password, str):
        raise ValueError("Password must be a string")
    if len(password) < _MIN_LEN:
        raise ValueError(f"Password must be at least {_MIN_LEN} characters")
    if len(password) > _MAX_LEN:
        raise ValueError(f"Password must be at most {_MAX_LEN} characters")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("Password must contain at least one letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one number")
    return password
