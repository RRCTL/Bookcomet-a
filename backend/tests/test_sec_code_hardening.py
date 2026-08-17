"""SEC-CODE-001..005 unit coverage for boot gates, uploads, and log redaction."""
from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import patch

import pytest

from app.core.config import (
    Settings,
    _validate_security_settings,
    is_tunnel_like_exposure,
)
from app.core.logging import SensitiveDataFilter
from app.services.file_storage import assert_file_type


class JwtAndInviteGatesTest(unittest.TestCase):
    def test_weak_jwt_rejected(self) -> None:
        s = Settings(
            jwt_secret_key="short",
            app_env="local",
            host="127.0.0.1",
            trust_forwarded_headers=False,
            register_invite_code="",
        )
        with self.assertRaises(RuntimeError) as ctx:
            with patch.dict(os.environ, {"CORS_ORIGINS": "http://127.0.0.1:5173"}, clear=False):
                _validate_security_settings(s)
        self.assertIn("JWT_SECRET_KEY", str(ctx.exception))

    def test_insecure_jwt_blocked_when_tunnel_like(self) -> None:
        s = Settings(
            jwt_secret_key="dev-secret-change-in-production",
            app_env="local",
            host="0.0.0.0",
            trust_forwarded_headers=False,
            register_invite_code="invite",
        )
        with patch.dict(
            os.environ,
            {"ALLOW_INSECURE_DEV_JWT": "true", "CORS_ORIGINS": "http://127.0.0.1:5173"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _validate_security_settings(s)
        self.assertIn("JWT_SECRET_KEY", str(ctx.exception))

    def test_invite_required_for_non_localhost_cors(self) -> None:
        s = Settings(
            jwt_secret_key="ci-test-jwt-secret-key-at-least-32-chars",
            app_env="local",
            host="127.0.0.1",
            trust_forwarded_headers=False,
            register_invite_code="",
        )
        with patch.dict(
            os.environ,
            {"CORS_ORIGINS": "https://app.example.com"},
            clear=False,
        ):
            self.assertTrue(is_tunnel_like_exposure(s))
            with self.assertRaises(RuntimeError) as ctx:
                _validate_security_settings(s)
        self.assertIn("REGISTER_INVITE_CODE", str(ctx.exception))

    def test_localhost_ok_without_invite(self) -> None:
        s = Settings(
            jwt_secret_key="ci-test-jwt-secret-key-at-least-32-chars",
            app_env="local",
            host="127.0.0.1",
            trust_forwarded_headers=False,
            register_invite_code="",
        )
        with patch.dict(
            os.environ,
            {"CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173"},
            clear=False,
        ):
            _validate_security_settings(s)  # does not raise

    def test_wildcard_cors_rejected(self) -> None:
        s = Settings(
            jwt_secret_key="ci-test-jwt-secret-key-at-least-32-chars",
            app_env="local",
            host="127.0.0.1",
            trust_forwarded_headers=False,
            register_invite_code="x",
        )
        with patch.dict(os.environ, {"CORS_ORIGINS": "*"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                _validate_security_settings(s)
        self.assertIn("CORS_ORIGINS", str(ctx.exception))


class UploadTypeTest(unittest.TestCase):
    def test_rejects_exe(self) -> None:
        with self.assertRaises(ValueError):
            assert_file_type("malware.exe", b"MZ\x90\x00fake")

    def test_rejects_extension_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            assert_file_type("photo.png", b"%PDF-1.4 fake")

    def test_accepts_png_magic(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        assert_file_type("ok.png", png)

    def test_accepts_pdf_magic(self) -> None:
        assert_file_type("stmt.pdf", b"%PDF-1.7\n%")


class LogRedactionTest(unittest.TestCase):
    def test_masks_api_key_and_bearer(self) -> None:
        filt = SensitiveDataFilter()
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="api_key=sk-abcdefghijklmnop bearer abc.def.ghi",
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(record))
        text = record.getMessage()
        self.assertNotIn("sk-abcdefghijklmnop", text)
        self.assertNotIn("abc.def.ghi", text)
        self.assertIn("***", text)


def test_cors_middleware_uses_explicit_methods():
    from app.main import CORS_ALLOW_HEADERS, CORS_ALLOW_METHODS

    assert "GET" in CORS_ALLOW_METHODS
    assert "*" not in CORS_ALLOW_METHODS
    assert "Authorization" in CORS_ALLOW_HEADERS
    assert "*" not in CORS_ALLOW_HEADERS
