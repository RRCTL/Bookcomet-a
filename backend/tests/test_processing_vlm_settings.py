"""AP / AR / BANK primary VLM follows Settings VLM_MODEL (fictional ids only)."""

import pytest

from app.core.config import (
    _DEFAULT_VLM_MODEL,
    resolve_layout_classify_model,
    resolve_ocr_provider,
    resolve_settings_vlm_model,
)


def test_resolve_uses_settings_vlm_when_mode_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    assert resolve_settings_vlm_model("") == "settings-vlm-fictional"
    assert resolve_settings_vlm_model(None) == "settings-vlm-fictional"
    assert resolve_settings_vlm_model("  ") == "settings-vlm-fictional"


def test_resolve_mode_override_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    assert resolve_settings_vlm_model("ap-override-fictional") == "ap-override-fictional"


def test_resolve_falls_back_to_builtin_when_settings_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VLM_MODEL", raising=False)
    assert resolve_settings_vlm_model() == _DEFAULT_VLM_MODEL


def test_ap_ar_bank_follow_settings_vlm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.delenv("AP_VLM_MODEL", raising=False)
    monkeypatch.delenv("AP_MULTI_RECEIPT_OCR_MODEL", raising=False)
    monkeypatch.delenv("AR_OCR_MODEL", raising=False)
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)

    from app.api import ocr as ocr_api

    assert ocr_api.resolve_ap_vlm_model() == "settings-vlm-fictional"
    assert ocr_api.resolve_ar_ocr_model() == "settings-vlm-fictional"
    assert resolve_settings_vlm_model("") == "settings-vlm-fictional"


def test_ap_explicit_env_still_overrides_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.setenv("AP_VLM_MODEL", "ap-only-fictional")
    monkeypatch.delenv("AR_OCR_MODEL", raising=False)
    from app.api import ocr as ocr_api

    assert ocr_api.resolve_ap_vlm_model() == "ap-only-fictional"
    # AR without AR_OCR_MODEL follows AP override, then Settings.
    assert ocr_api.resolve_ar_ocr_model() == "ap-only-fictional"


def test_registry_registers_settings_vlm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.delenv("AP_VLM_MODEL", raising=False)
    monkeypatch.delenv("AP_MULTI_RECEIPT_OCR_MODEL", raising=False)
    monkeypatch.delenv("AR_OCR_MODEL", raising=False)
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    monkeypatch.delenv("BANK_CROSS_VLM_MODEL", raising=False)
    monkeypatch.delenv("AP_CROSS_VLM_MODEL", raising=False)
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    monkeypatch.delenv("DOCUMENT_LAYOUT_CLASSIFY_MODEL", raising=False)
    from app.ocr.providers import OcrProviderRegistry

    reg = OcrProviderRegistry()
    assert reg.get("settings-vlm-fictional") is not None


def test_layout_classify_follows_settings_vlm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.delenv("DOCUMENT_LAYOUT_CLASSIFY_MODEL", raising=False)
    assert resolve_layout_classify_model() == "settings-vlm-fictional"


def test_layout_classify_env_overrides_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.setenv("DOCUMENT_LAYOUT_CLASSIFY_MODEL", "layout-only-fictional")
    assert resolve_layout_classify_model() == "layout-only-fictional"


def test_empty_ocr_provider_follows_settings_vlm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    assert resolve_ocr_provider() == "settings-vlm-fictional"


def test_explicit_ocr_provider_kept(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.setenv("OCR_PROVIDER", "easy")
    assert resolve_ocr_provider() == "easy"


def test_sync_refreshes_layout_and_ocr_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_MODEL", "settings-vlm-fictional")
    monkeypatch.delenv("DOCUMENT_LAYOUT_CLASSIFY_MODEL", raising=False)
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    from app.core.config import settings
    from app.core.gateway_settings import _sync_settings_from_env

    prev_ocr = settings.ocr_provider
    prev_layout = settings.document_layout_classify_model
    prev_vlm = settings.vlm_model
    try:
        _sync_settings_from_env()
        assert settings.document_layout_classify_model == "settings-vlm-fictional"
        assert settings.ocr_provider == "settings-vlm-fictional"
    finally:
        settings.ocr_provider = prev_ocr
        settings.document_layout_classify_model = prev_layout
        settings.vlm_model = prev_vlm
