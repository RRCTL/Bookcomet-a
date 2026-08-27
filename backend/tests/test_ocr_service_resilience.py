from __future__ import annotations

import pytest

from app.ocr.interfaces import OcrResult
from app.services.ocr_service import OcrService


class _Flaky429Provider:
    def __init__(self) -> None:
        self.calls = 0
        self._base_url = "https://a/v1"
        self._model = "m1"

    async def recognize(self, *_args, **_kwargs) -> OcrResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("OCR_HTTP_429: too many")
        return OcrResult(text="ok", lines=[], metadata={})


class _AlwaysFailProvider:
    def __init__(self, *, base_url: str, model: str, msg: str) -> None:
        self.calls = 0
        self._base_url = base_url
        self._model = model
        self._msg = msg

    async def recognize(self, *_args, **_kwargs) -> OcrResult:
        self.calls += 1
        raise RuntimeError(self._msg)


class _Registry:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def get(self, name: str) -> object:
        return self._mapping[name]


@pytest.mark.asyncio
async def test_primary_retries_on_http_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "1")
    svc = OcrService()
    flaky = _Flaky429Provider()
    svc._registry = _Registry({"qwen-vl-ocr-latest": flaky})  # type: ignore[attr-defined]
    out = await svc.recognize("fake.png", provider_name="qwen-vl-ocr-latest")
    assert out.text == "ok"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_skip_identical_fallback_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "0")
    monkeypatch.setenv("VLM_MODEL", "same-model")
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
    # Fallback candidates are only DeepSeek-OCR + settings.ocr_provider — same
    # endpoint/model is skipped, so no successful fallback → raise.
    with pytest.raises(RuntimeError) as exc:
        await svc.recognize(
            "fake.png",
            provider_name="qwen-vl-ocr-latest",
            model="same-model",
        )
    assert same_fallback.calls == 0
    assert "no fallbacks attempted" in str(exc.value) or "Fallbacks failed" in str(exc.value)
