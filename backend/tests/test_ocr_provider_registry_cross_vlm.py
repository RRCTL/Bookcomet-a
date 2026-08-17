"""BANK_CROSS_VLM_* registers in OcrProviderRegistry (Strategy B second pass)."""
import pytest


def test_cross_vlm_model_registered_with_main_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("BANK_CROSS_VLM_MODEL", "my-cross-model-test")
    monkeypatch.delenv("BANK_CROSS_VLM_API_KEY", raising=False)
    monkeypatch.delenv("BANK_CROSS_VLM_BASE_URL", raising=False)
    from app.ocr.providers import OcrProviderRegistry

    reg = OcrProviderRegistry()
    assert reg.get("my-cross-model-test") is not None


def test_cross_vlm_separate_gateway_registered(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_API_KEY", "main-key")
    monkeypatch.setenv("BANK_CROSS_VLM_MODEL", "cross-model-only")
    monkeypatch.setenv("BANK_CROSS_VLM_API_KEY", "cross-key")
    monkeypatch.setenv("BANK_CROSS_VLM_BASE_URL", "https://cross.example.com")
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    from app.ocr.providers import OcrProviderRegistry

    reg = OcrProviderRegistry()
    p = reg.get("cross-model-only")
    assert p._api_key == "cross-key"
    assert p._base_url == "https://cross.example.com/v1"


def test_cross_vlm_not_registered_when_model_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_API_KEY", "unit-test-key")
    monkeypatch.delenv("BANK_CROSS_VLM_MODEL", raising=False)
    monkeypatch.delenv("BANK_CROSS_VLM_API_KEY", raising=False)
    monkeypatch.delenv("BANK_CROSS_VLM_BASE_URL", raising=False)
    # AP cross can share the same model id; clear so this test stays bank-cross focused.
    monkeypatch.delenv("AP_CROSS_VLM_MODEL", raising=False)
    monkeypatch.delenv("AP_CROSS_VLM_API_KEY", raising=False)
    monkeypatch.delenv("AP_CROSS_VLM_BASE_URL", raising=False)
    from app.ocr.providers import OcrProviderRegistry

    reg = OcrProviderRegistry()
    with pytest.raises(ValueError, match="OCR provider not found"):
        reg.get("doubao-seed-2-0-lite-260215")


def test_cross_vlm_registry_collision_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VLM_API_KEY", "main-key")
    monkeypatch.setenv("BANK_VLM_MODEL", "same-id")
    monkeypatch.setenv("BANK_CROSS_VLM_MODEL", "same-id")
    monkeypatch.setenv("BANK_CROSS_VLM_API_KEY", "other-key")
    from app.ocr.providers import OcrProviderRegistry

    with pytest.raises(RuntimeError, match="BANK_CROSS_VLM_MODEL must differ"):
        OcrProviderRegistry()
