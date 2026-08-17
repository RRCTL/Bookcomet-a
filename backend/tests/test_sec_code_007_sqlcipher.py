"""SEC-CODE-007: optional SQLCipher / DATABASE_PASSWORD for SQLite."""
from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db_url import (
    build_sqlalchemy_url,
    create_db_engine,
    looks_like_plaintext_sqlite,
    sqlite_file_path,
)


class DbUrlHelpersTest(unittest.TestCase):
    def test_sqlite_file_path(self) -> None:
        self.assertEqual(
            sqlite_file_path("sqlite:///./ai_accounting.db"),
            Path("./ai_accounting.db"),
        )
        self.assertIsNone(sqlite_file_path("sqlite:///:memory:"))
        self.assertIsNone(sqlite_file_path("postgresql://x/y"))

    def test_password_too_short(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            build_sqlalchemy_url("sqlite:///./x.db", password="short")
        self.assertIn("DATABASE_PASSWORD", str(ctx.exception))

    def test_no_password_keeps_plain_url(self) -> None:
        url, extra = build_sqlalchemy_url(
            "sqlite:///./ai_accounting.db", password=""
        )
        self.assertEqual(url, "sqlite:///./ai_accounting.db")
        self.assertEqual(extra, {})

    def test_rejects_plaintext_file_when_password_set(self) -> None:
        root = Path(os.environ.get("TMPDIR") or "/tmp")
        path = root / f"bc-plain-{os.getpid()}.db"
        try:
            conn = sqlite3.connect(str(path))
            conn.execute("create table t(x int)")
            conn.commit()
            conn.close()
            self.assertTrue(looks_like_plaintext_sqlite(path))
            with self.assertRaises(RuntimeError) as ctx:
                build_sqlalchemy_url(
                    f"sqlite:///{path.as_posix()}",
                    password="long-enough-password-16",
                )
            self.assertIn("plaintext", str(ctx.exception).lower())
            self.assertIn("encrypt_sqlite_db", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)


def test_sqlcipher_round_trip(tmp_path: Path) -> None:
    sqlcipher3 = pytest.importorskip("sqlcipher3")
    db_path = tmp_path / "secure.db"
    password = "ci-test-database-password-32chars"
    url, extra = build_sqlalchemy_url(
        f"sqlite:///{db_path.as_posix()}",
        password=password,
    )
    assert url.startswith("sqlite+pysqlcipher://")
    assert extra.get("module") is sqlcipher3

    engine = create_db_engine(
        f"sqlite:///{db_path.as_posix()}",
        password=password,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (7)"))

    # Wrong password fails
    with pytest.raises(Exception):
        bad = create_db_engine(
            f"sqlite:///{db_path.as_posix()}",
            password="wrong-password-not-it-16",
            connect_args={"check_same_thread": False},
        )
        with bad.connect() as conn:
            conn.execute(text("SELECT x FROM t")).scalar()

    # Correct password reads data; plaintext sqlite3 cannot
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM t")).scalar() == 7
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(str(db_path)).execute("SELECT * FROM sqlite_master").fetchall()


def test_encrypt_script_migrates_plaintext(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("sqlcipher3")
    from scripts.encrypt_sqlite_db import _encrypt

    src = tmp_path / "plain.db"
    dest = tmp_path / "enc.db"
    conn = sqlite3.connect(str(src))
    conn.execute("create table items(id int)")
    conn.execute("insert into items values (99)")
    conn.commit()
    conn.close()

    password = "migrate-password-16+"
    _encrypt(src, dest, password)

    engine = create_db_engine(
        f"sqlite:///{dest.as_posix()}",
        password=password,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as c:
        assert c.execute(text("select id from items")).scalar() == 99
