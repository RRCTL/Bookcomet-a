"""BANK_VLM_MODEL follows Settings → API VLM_MODEL unless explicitly overridden."""

from __future__ import annotations

import pytest

from app.core.config import _DEFAULT_VLM_MODEL, resolve_bank_vlm_model


def test_resolve_bank_vlm_uses_vlm_model_when_bank_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.setenv("VLM_MODEL", "settings-api-vlm-model")
    assert resolve_bank_vlm_model() == "settings-api-vlm-model"


def test_resolve_bank_vlm_explicit_override_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BANK_VLM_MODEL", "bank-override-model")
    monkeypatch.setenv("VLM_MODEL", "settings-api-vlm-model")
    assert resolve_bank_vlm_model() == "bank-override-model"


def test_resolve_bank_vlm_falls_back_to_vlm_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)
    assert resolve_bank_vlm_model() == _DEFAULT_VLM_MODEL


def test_runtime_refresh_picks_up_vlm_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.setenv("VLM_MODEL", "refreshed-vlm-from-api")
    monkeypatch.setenv("VLM_API_KEY", "unit-test-key")
    from app.ocr import runtime as rt

    rt.refresh_ai_runtime()
    assert rt.BANK_VLM_MODEL == "refreshed-vlm-from-api"
