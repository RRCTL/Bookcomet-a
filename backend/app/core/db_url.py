"""
Database URL helpers (SEC-CODE-007).

When DATABASE_PASSWORD (or SQLITE_KEY) is set and DATABASE_URL is a SQLite
file URL, the app uses SQLCipher via sqlcipher3 so the DB file is encrypted
at rest (laptop theft / shared PC).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

_MIN_DB_PASSWORD_LEN = 16


def database_password() -> str:
    return (os.getenv("DATABASE_PASSWORD") or os.getenv("SQLITE_KEY") or "").strip()


def is_sqlite_url(database_url: str) -> bool:
    return (database_url or "").strip().lower().startswith("sqlite")


def sqlite_file_path(database_url: str) -> Path | None:
    """Return the filesystem path for a sqlite:/// URL, or None for memory/other."""
    url = (database_url or "").strip()
    if not is_sqlite_url(url):
        return None
    # Support sqlite:///relative, sqlite:////absolute, sqlite:///:memory:
    if ":///" not in url:
        return None
    path_part = url.split(":///", 1)[1]
    if not path_part or path_part.startswith(":memory:"):
        return None
    # Strip query string if present
    path_part = path_part.split("?", 1)[0]
    return Path(path_part)


def resolve_sqlite_path(database_url: str, *, base_dir: Path | None = None) -> Path | None:
    path = sqlite_file_path(database_url)
    if path is None:
        return None
    if path.is_absolute():
        return path
    root = base_dir if base_dir is not None else Path.cwd()
    return (root / path).resolve()


def looks_like_plaintext_sqlite(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


def build_sqlalchemy_url(
    database_url: str,
    *,
    password: str | None = None,
    base_dir: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Return (sqlalchemy_url, create_engine_kwargs).

    When a password is present and the URL is SQLite file-backed, switches to
    sqlite+pysqlcipher and passes module=sqlcipher3.
    """
    url = (database_url or "").strip() or "sqlite:///./ai_accounting.db"
    pwd = database_password() if password is None else (password or "").strip()
    extra: dict[str, Any] = {}

    if not pwd:
        return url, extra

    if not is_sqlite_url(url):
        # Non-SQLite engines keep credentials in DATABASE_URL itself.
        return url, extra

    if len(pwd) < _MIN_DB_PASSWORD_LEN:
        raise RuntimeError(
            f"DATABASE_PASSWORD must be at least {_MIN_DB_PASSWORD_LEN} characters "
            "when using SQLite encryption (SEC-CODE-007). "
            "Generate one with: openssl rand -base64 32"
        )

    path = resolve_sqlite_path(url, base_dir=base_dir)
    if path is None:
        raise RuntimeError(
            "DATABASE_PASSWORD cannot be used with in-memory SQLite URLs. "
            "Point DATABASE_URL at a file path, or clear DATABASE_PASSWORD."
        )

    if path.exists() and looks_like_plaintext_sqlite(path):
        raise RuntimeError(
            f"SQLite file {path} is plaintext but DATABASE_PASSWORD is set. "
            "Migrate with: python scripts/encrypt_sqlite_db.py "
            f"--src {path}  (see LOCAL_DEV_SETUP.md SEC-CODE-007)."
        )

    try:
        import sqlcipher3
    except ImportError as exc:  # pragma: no cover - dependency should be installed
        raise RuntimeError(
            "DATABASE_PASSWORD is set but sqlcipher3 is not installed. "
            "Run: pip install sqlcipher3"
        ) from exc

    encoded = quote(pwd, safe="")
    cipher_url = f"sqlite+pysqlcipher://:{encoded}@/{path.as_posix()}"
    extra["module"] = sqlcipher3
    return cipher_url, extra


def create_db_engine(
    database_url: str,
    *,
    password: str | None = None,
    base_dir: Path | None = None,
    **engine_kwargs: Any,
):
    """create_engine with optional SQLCipher wiring."""
    from sqlalchemy import create_engine

    url, extra = build_sqlalchemy_url(
        database_url, password=password, base_dir=base_dir
    )
    merged = {**engine_kwargs, **extra}
    return create_engine(url, **merged)
