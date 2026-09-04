from __future__ import annotations

import pytest

from app.ocr.interfaces import OcrResult
from app.services.ocr_service import OcrService


class _FlakyProvider:
    def __init__(self) -> None:
        self.calls = 0
        self._base_url = "https://a/v1"
        self._model = "m1"

    async def recognize(self, *_args, **_kwargs) -> OcrResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("OCR_EMPTY_CONTENT: transient")
        return OcrResult(text="ok", lines=[], metadata={})


class _AlwaysFailProvider:
    def __init__(self, *, base_url: str, model: str, msg: str) -> None:
        self._base_url = base_url
        self._model = model
        self._msg = msg

    async def recognize(self, *_args, **_kwargs) -> OcrResult:
        raise RuntimeError(self._msg)


class _Registry:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def get(self, name: str) -> object:
        return self._mapping[name]


@pytest.mark.asyncio
async def test_primary_does_not_retry_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "1")
    svc = OcrService()
    flaky = _FlakyProvider()
    alt = _AlwaysFailProvider(
        base_url="https://other/v1",
        model="other-model",
        msg="OCR_REQUEST_ERROR: alt fail",
    )
    svc._registry = _Registry(  # type: ignore[attr-defined]
        {
            "qwen-vl-ocr-latest": flaky,
            "DeepSeek-OCR": alt,
            "qwen3-vl-plus": alt,
        }
    )
    with pytest.raises(RuntimeError) as exc:
        await svc.recognize("fake.png", provider_name="qwen-vl-ocr-latest")
    assert "OCR_EMPTY_CONTENT" in str(exc.value) or "Fallbacks failed" in str(exc.value)
    assert flaky.calls == 1


@pytest.mark.asyncio
async def test_skip_identical_fallback_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "0")
    svc = OcrService()
    primary = _AlwaysFailProvider(
        base_url="https://same/v1",
        model="same-model",
        msg="OCR_REQUEST_ERROR: hard fail",
    )
    same_fallback = _AlwaysFailProvider(
        base_url="https://same/v1",
        model="same-model",
        msg="OCR_REQUEST_ERROR: fallback would be same",
    )
    alt_fallback = _AlwaysFailProvider(
        base_url="https://other/v1",
        model="other-model",
        msg="OCR_REQUEST_ERROR: alt fail",
    )
    svc._registry = _Registry(  # type: ignore[attr-defined]
        {
            "qwen-vl-ocr-latest": primary,
            "DeepSeek-OCR": same_fallback,
            "qwen3-vl-plus": alt_fallback,
        }
    )
    with pytest.raises(RuntimeError) as exc:
        await svc.recognize("fake.png", provider_name="qwen-vl-ocr-latest")
    assert "Fallbacks failed" in str(exc.value)
