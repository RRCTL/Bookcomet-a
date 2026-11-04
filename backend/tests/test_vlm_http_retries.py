"""Provider HTTP retry count comes from ocr_options / env (synthetic, no gateway)."""

from __future__ import annotations

import pytest
import requests


@pytest.mark.asyncio
async def test_http_max_retries_one_does_not_loop_timeouts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("VLM_BASE_URL", "https://example.com/api")
    monkeypatch.setenv("VLM_MODEL", "unit-vlm")
    monkeypatch.setenv("VLM_READ_TIMEOUT", "1")
    from PIL import Image

    img = tmp_path / "c1.jpg"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(img, format="JPEG")

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise requests.exceptions.Timeout("unit-timeout")

    monkeypatch.setattr("app.ocr.providers.requests.post", boom)
    from app.ocr.providers import DeepSeekOcrProvider

    provider = DeepSeekOcrProvider(api_key="unit-test-key")
    with pytest.raises(RuntimeError, match="OCR_REQUEST_ERROR"):
        await provider.recognize(
            str(img),
            ocr_options={"http_max_retries": 1},
            image_options={"max_side": 0, "format": "JPEG", "quality": 80},
        )
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_http_max_retries_default_tries_three(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("VLM_BASE_URL", "https://example.com/api")
    monkeypatch.setenv("VLM_MODEL", "unit-vlm")
    monkeypatch.delenv("VLM_HTTP_MAX_RETRIES", raising=False)
    from PIL import Image

    img = tmp_path / "c1.jpg"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(img, format="JPEG")

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise requests.exceptions.Timeout("unit-timeout")

    monkeypatch.setattr("app.ocr.providers.requests.post", boom)
    from app.ocr.providers import DeepSeekOcrProvider

    provider = DeepSeekOcrProvider(api_key="unit-test-key")
    with pytest.raises(RuntimeError, match="OCR_REQUEST_ERROR"):
        await provider.recognize(
            str(img),
            image_options={"max_side": 0, "format": "JPEG", "quality": 80},
        )
    assert calls["n"] == 3
