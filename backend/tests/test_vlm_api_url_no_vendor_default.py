"""VLM API URL must not default to a vendor host in Settings or env sync."""
from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.core.gateway_settings import _sync_settings_from_env, stored_gateway


_VENDOR_DEFAULT = "https://www.dmxapi.cn"


def _clear_gateway_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VLM_BASE_URL",
        "LLM_BASE_URL",
        "DEPLOY_BASE_URL",
        "AI_ENHANCE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
        # Also clear empty-string leftovers from prior tests / .env loading.
        if name in os.environ:
            monkeypatch.delenv(name, raising=False)


def test_stored_vlm_api_url_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gateway_urls(monkeypatch)
    monkeypatch.setenv("VLM_BASE_URL", "")
    assert stored_gateway("vlm")["api_url"] == ""


def test_sync_does_not_inject_vendor_vlm_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gateway_urls(monkeypatch)
    monkeypatch.setenv("VLM_BASE_URL", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    _sync_settings_from_env()
    assert settings.vlm_api_base == ""
    assert (os.getenv("VLM_BASE_URL") or "") == ""
    assert _VENDOR_DEFAULT not in (settings.vlm_api_base, os.getenv("VLM_BASE_URL") or "")


def test_runtime_module_no_longer_injects_vlm_base_url() -> None:
    """Regression: ocr.runtime used to copy settings.vlm_api_base (dmxapi) into env."""
    import app.ocr.runtime as runtime

    assert not hasattr(runtime, "_ensure_ocr_base_url")
