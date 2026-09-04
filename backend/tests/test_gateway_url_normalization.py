"""OpenAI-compatible gateway URL helpers — Settings→API source of truth."""
from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.core.gateway_settings import (
    _sync_settings_from_env,
    normalize_openai_base_url,
    openai_chat_completions_url,
    openai_models_url,
)


_VENDOR_DEFAULT = "https://www.dmxapi.cn"


def test_normalize_empty_stays_empty() -> None:
    assert normalize_openai_base_url("") == ""
    assert normalize_openai_base_url("   ") == ""


def test_normalize_appends_v1_once() -> None:
    assert normalize_openai_base_url("https://example.com/api") == "https://example.com/api/v1"
    assert (
        normalize_openai_base_url("https://example.com/api/v1") == "https://example.com/api/v1"
    )
    assert (
        normalize_openai_base_url("https://example.com/api/v1/") == "https://example.com/api/v1"
    )


def test_chat_completions_url_no_double_v1() -> None:
    assert (
        openai_chat_completions_url("https://openrouter.ai/api/v1")
        == "https://openrouter.ai/api/v1/chat/completions"
    )
    assert (
        openai_chat_completions_url("https://openrouter.ai/api")
        == "https://openrouter.ai/api/v1/chat/completions"
    )
    assert (
        openai_chat_completions_url("https://openrouter.ai/api/v1/")
        == "https://openrouter.ai/api/v1/chat/completions"
    )


def test_models_url_no_double_v1() -> None:
    assert openai_models_url("https://example.com/api/v1") == "https://example.com/api/v1/models"
    assert openai_models_url("https://example.com/api") == "https://example.com/api/v1/models"


def test_chat_completions_url_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        openai_chat_completions_url("")


def test_sync_does_not_inject_vendor_ai_enhance_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VLM_BASE_URL",
        "LLM_BASE_URL",
        "DEPLOY_BASE_URL",
        "AI_ENHANCE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VLM_BASE_URL", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "")
    _sync_settings_from_env()
    assert settings.ai_enhance_api_base == ""
    assert _VENDOR_DEFAULT not in (settings.ai_enhance_api_base or "")


def test_ai_enhance_client_uses_shared_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: …/api/v1 must not become …/api/v1/v1/chat/completions."""
    captured: dict = {}

    def _fake_post(url, *args, **kwargs):
        captured["url"] = url

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"content": "{}"}}],
                }

        return _Resp()

    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-test-key")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("AI_ENHANCE_MODEL", "test-model")
    monkeypatch.setattr("app.services.ai_enhance_client.requests.post", _fake_post)

    from app.services.ai_enhance_client import AiEnhanceClient

    client = AiEnhanceClient(
        api_key="sk-test-key",
        base_url="https://openrouter.ai/api/v1",
        default_model="test-model",
    )
    client.chat_completions(messages=[{"role": "user", "content": "hi"}], max_tokens=8)
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert "/v1/v1/" not in captured["url"]


def test_ocr_normalize_delegates_no_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ocr.providers import _normalize_ocr_base_url

    assert _normalize_ocr_base_url("") == ""
    assert _normalize_ocr_base_url("https://example.com/api") == "https://example.com/api/v1"
    assert _VENDOR_DEFAULT not in _normalize_ocr_base_url("")
