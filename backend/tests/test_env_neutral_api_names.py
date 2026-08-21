"""Neutral gateway env names (VLM_* / LLM_* / AI_ENHANCE_*) for AI clients."""
import pytest


def _clear_vlm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VLM_API_KEY", "VLM_BASE_URL", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "DEPLOY_API_KEY", "DEPLOY_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_provider_prefers_vlm_api_key(monkeypatch: pytest.MonkeyPatch):
    _clear_vlm_env(monkeypatch)
    monkeypatch.setenv("VLM_API_KEY", "new-key")
    monkeypatch.setenv("LLM_API_KEY", "other-key")
    monkeypatch.setenv("VLM_BASE_URL", "https://new.example.com")
    from app.ocr.providers import DeepSeekOcrProvider

    p = DeepSeekOcrProvider()
    assert p._api_key == "new-key"
    assert p._base_url.startswith("https://new.example.com")


def test_provider_falls_back_to_llm_api_key(monkeypatch: pytest.MonkeyPatch):
    _clear_vlm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    from app.ocr.providers import DeepSeekOcrProvider

    p = DeepSeekOcrProvider()
    assert p._api_key == "llm-key"


def test_provider_constructs_without_any_key(monkeypatch: pytest.MonkeyPatch):
    """Empty VLM/LLM keys must not block API boot / registry construction."""
    _clear_vlm_env(monkeypatch)
    monkeypatch.setenv("VLM_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    from app.ocr.providers import DeepSeekOcrProvider, OcrProviderRegistry

    p = DeepSeekOcrProvider()
    assert p._api_key == ""
    reg = OcrProviderRegistry()
    assert reg.get("DeepSeek-OCR") is not None


@pytest.mark.asyncio
async def test_provider_recognize_requires_key(monkeypatch: pytest.MonkeyPatch):
    _clear_vlm_env(monkeypatch)
    monkeypatch.setenv("VLM_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    from app.ocr.providers import DeepSeekOcrProvider

    p = DeepSeekOcrProvider()
    with pytest.raises(RuntimeError, match="VLM_API_KEY"):
        await p.recognize("/nonexistent.png")


def test_chat_client_prefers_llm_names(monkeypatch: pytest.MonkeyPatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "new-llm-key")
    monkeypatch.setenv("DEPLOY_API_KEY", "old-deploy-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/")
    monkeypatch.setenv("DEPLOY_BASE_URL", "https://deploy.example.com")
    from app.services.ai_chat_client import DeployChatClient

    c = DeployChatClient()
    assert c.api_key == "new-llm-key"
    assert c.base_url == "https://llm.example.com"


def test_chat_client_falls_back_to_legacy_deploy_names(monkeypatch: pytest.MonkeyPatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("DEPLOY_API_KEY", "old-deploy-key")
    monkeypatch.setenv("DEPLOY_BASE_URL", "https://deploy.example.com")
    from app.services.ai_chat_client import DeployChatClient

    c = DeployChatClient()
    assert c.api_key == "old-deploy-key"
    assert c.base_url == "https://deploy.example.com"


def test_provider_transport_uses_vlm_names(monkeypatch: pytest.MonkeyPatch):
    _clear_vlm_env(monkeypatch)
    monkeypatch.setenv("VLM_API_KEY", "k")
    monkeypatch.setenv("VLM_TIMEOUT", "33")
    monkeypatch.setenv("VLM_MAX_SIDE", "1234")
    monkeypatch.setenv("VLM_IMAGE_FORMAT", "jpeg")
    from app.ocr.providers import DeepSeekOcrProvider

    p = DeepSeekOcrProvider()
    assert p._timeout == 33.0
    assert p._max_side == 1234
    assert p._image_format == "JPEG"


def test_ai_enhance_key_preferred_for_ai_enhance_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "enhance-key")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient()
    assert svc._api_key == "enhance-key"


def test_ai_enhance_client_falls_back_to_llm_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AI_ENHANCE_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient()
    assert svc._api_key == "llm-key"


def test_ai_enhance_client_falls_back_to_vlm_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AI_ENHANCE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("VLM_API_KEY", "vlm-key")
    monkeypatch.setenv("AI_ENHANCE_MODEL", "test-model")
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient()
    assert svc._api_key == "vlm-key"


def test_ai_enhance_client_accepts_env_model_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-test-key")
    monkeypatch.setenv("AI_ENHANCE_MODEL", "qwen3.5-35b-a3b")
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient()
    assert svc._default_model == "qwen3.5-35b-a3b"
    svc._validate_input("qwen3.5-35b-a3b", [{"role": "user", "content": "ping"}], 0.1, 16)


def test_ai_enhance_client_disables_thinking_for_qwen3(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-test-key")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "https://example.com")
    captured: dict = {}

    def _fake_post(*_args, **kwargs):
        captured["json"] = kwargs.get("json") or {}

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "{}"}}]}

        return _Resp()

    monkeypatch.setattr("app.services.ai_enhance_client.requests.post", _fake_post)
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient(default_model="qwen3.5-35b-a3b")
    svc.chat_completions(messages=[{"role": "user", "content": "hi"}])
    assert captured["json"].get("enable_thinking") is False


def test_ai_enhance_client_respects_explicit_enable_thinking(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-test-key")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "https://example.com")
    captured: dict = {}

    def _fake_post(*_args, **kwargs):
        captured["json"] = kwargs.get("json") or {}

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "{}"}}]}

        return _Resp()

    monkeypatch.setattr("app.services.ai_enhance_client.requests.post", _fake_post)
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient(default_model="qwen3.5-35b-a3b")
    svc.chat_completions(
        messages=[{"role": "user", "content": "hi"}],
        enable_thinking=True,
    )
    assert captured["json"].get("enable_thinking") is True


def test_ai_enhance_client_uses_vlm_read_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-test-key")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "https://example.com")
    monkeypatch.setenv("VLM_READ_TIMEOUT", "360")
    monkeypatch.setenv("VLM_CONNECT_TIMEOUT", "20")
    captured: dict = {}

    def _fake_post(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "{}"}}]}

        return _Resp()

    monkeypatch.setattr("app.services.ai_enhance_client.requests.post", _fake_post)
    from app.services.ai_enhance_client import AiEnhanceClient

    svc = AiEnhanceClient(default_model="qwen-chat")
    svc.chat_completions(messages=[{"role": "user", "content": "hi"}])
    assert captured["timeout"] == (20.0, 360.0)
