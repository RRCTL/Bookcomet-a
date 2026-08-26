"""Slice 3: BANK / Cross-VLM settings must fail closed (no silent defaults)."""

from __future__ import annotations

import pytest

from app.core.config import (
    require_bank_cross_vlm_settings,
    require_bank_vlm_settings,
    resolve_bank_vlm_model,
)


def test_resolve_bank_vlm_uses_settings_vlm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.setenv("VLM_MODEL", "from-api-settings")
    assert resolve_bank_vlm_model(fail_closed=True) == "from-api-settings"


def test_require_bank_vlm_fails_when_model_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)
    monkeypatch.setenv("VLM_API_KEY", "k")
    monkeypatch.setenv("VLM_BASE_URL", "https://example.test")
    with pytest.raises(ValueError, match="not configured"):
        require_bank_vlm_settings()


def test_require_bank_vlm_fails_when_key_or_url_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "m")
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.setenv("VLM_API_KEY", "k")
    monkeypatch.delenv("VLM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="API URL"):
        require_bank_vlm_settings()


def test_require_bank_vlm_ok_when_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "m")
    monkeypatch.setenv("VLM_API_KEY", "k")
    monkeypatch.setenv("VLM_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    cfg = require_bank_vlm_settings()
    assert cfg["model"] == "m"
    assert cfg["api_url"].startswith("https://example.test")


def test_cross_vlm_disabled_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_CROSS_VLM_MODEL", raising=False)
    monkeypatch.setenv("BANK_CROSS_VLM_VERIFY", "false")
    assert require_bank_cross_vlm_settings() is None


def test_cross_vlm_verify_without_model_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_CROSS_VLM_MODEL", raising=False)
    monkeypatch.setenv("BANK_CROSS_VLM_VERIFY", "true")
    with pytest.raises(ValueError, match="Cross-VLM"):
        require_bank_cross_vlm_settings()


def test_cross_vlm_requires_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BANK_CROSS_VLM_MODEL", "cross-m")
    monkeypatch.setenv("BANK_CROSS_VLM_VERIFY", "true")
    monkeypatch.delenv("BANK_CROSS_VLM_API_KEY", raising=False)
    monkeypatch.delenv("BANK_CROSS_VLM_BASE_URL", raising=False)
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.delenv("VLM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="credentials"):
        require_bank_cross_vlm_settings()
