#!/usr/bin/env python3
"""
SEC-CODE-007 — migrate a plaintext SQLite file to SQLCipher.

Usage (from backend/):

  set DATABASE_PASSWORD=<openssl rand -base64 32>
  python scripts/encrypt_sqlite_db.py
  # or:
  python scripts/encrypt_sqlite_db.py --src ./ai_accounting.db --dest ./ai_accounting.enc.db

Then point DATABASE_URL at the encrypted file (or replace the original after backup)
and keep DATABASE_PASSWORD in backend/.env (never commit it).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Allow `python scripts/encrypt_sqlite_db.py` from backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.db_url import (  # noqa: E402
    database_password,
    looks_like_plaintext_sqlite,
    resolve_sqlite_path,
)


def _encrypt(src: Path, dest: Path, password: str) -> None:
    import sqlcipher3

    if dest.exists():
        raise SystemExit(f"Destination already exists: {dest}")
    if not src.is_file():
        raise SystemExit(f"Source not found: {src}")
    if not looks_like_plaintext_sqlite(src):
        raise SystemExit(
            f"Source does not look like a plaintext SQLite file: {src}"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlcipher3.connect(str(src))
    try:
        # sqlcipher_export copies schema + data into the attached encrypted DB.
        conn.execute(
            "ATTACH DATABASE ? AS encrypted KEY ?",
            (str(dest), password),
        )
        conn.execute("SELECT sqlcipher_export('encrypted')")
        conn.execute("DETACH DATABASE encrypted")
    finally:
        conn.close()

    # Verify round-trip with the same engine wiring the app uses.
    from sqlalchemy import text

    from app.core.db_url import create_db_engine

    engine = create_db_engine(
        f"sqlite:///{dest.as_posix()}",
        password=password,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as conn:
        conn.execute(text("SELECT count(*) FROM sqlite_master")).scalar()
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypt a SQLite DB with SQLCipher")
    parser.add_argument(
        "--src",
        default=None,
        help="Plaintext SQLite path (default: path from DATABASE_URL)",
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="Encrypted output path (default: <src>.encrypted)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Backup src to <src>.plaintext.bak and replace src with encrypted file",
    )
    args = parser.parse_args()

    password = database_password()
    if len(password) < 16:
        raise SystemExit(
            "Set DATABASE_PASSWORD (or SQLITE_KEY) to a secret of at least 16 characters "
            "before running this script."
        )

    if args.src:
        src = Path(args.src).expanduser().resolve()
    else:
        db_url = os.getenv("DATABASE_URL", "sqlite:///./ai_accounting.db")
        resolved = resolve_sqlite_path(db_url, base_dir=Path.cwd())
        if resolved is None:
            raise SystemExit("Could not resolve a SQLite file path from DATABASE_URL")
        src = resolved

    dest = (
        Path(args.dest).expanduser().resolve()
        if args.dest
        else src.with_suffix(src.suffix + ".encrypted")
    )

    print(f"Encrypting {src} → {dest}")
    _encrypt(src, dest, password)
    print("Encryption OK.")

    if args.replace:
        bak = src.with_suffix(src.suffix + ".plaintext.bak")
        if bak.exists():
            raise SystemExit(f"Backup path already exists: {bak}")
        shutil.move(str(src), str(bak))
        shutil.move(str(dest), str(src))
        print(f"Replaced {src}; plaintext backup at {bak}")
        print("Keep DATABASE_PASSWORD in backend/.env and restart the API.")
    else:
        print(
            f"Next: set DATABASE_URL to sqlite:///{dest.as_posix()} "
            "(or re-run with --replace), keep DATABASE_PASSWORD set, restart API."
        )


if __name__ == "__main__":
    main()
