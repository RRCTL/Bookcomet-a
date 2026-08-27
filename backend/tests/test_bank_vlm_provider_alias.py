"""BANK VLM must use OCR gateway provider alias, not the model id as provider name."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.ocr.interfaces import OcrResult
from app.ocr.runtime import resolve_bank_vlm_provider_name
from app.services.ocr_service import OcrService


def test_resolve_bank_vlm_provider_falls_back_to_ocr_gateway(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "ocr_provider", "qwen-vl-ocr-latest")
    monkeypatch.setenv("VLM_MODEL", "qwen3.6-35b-a3b")
    monkeypatch.delenv("BANK_VLM_MODEL", raising=False)
    # Primary BANK model id must resolve to gateway alias, never to itself.
    name = resolve_bank_vlm_provider_name("qwen3.6-35b-a3b")
    assert name == "qwen-vl-ocr-latest"
    assert name != "qwen3.6-35b-a3b"


@pytest.mark.asyncio
async def test_recognize_accepts_unregistered_model_as_model_override(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: BANK used to pass model id as provider_name and crash."""
    monkeypatch.setenv("OCR_PROVIDER", "qwen-vl-ocr-latest")
    svc = OcrService()

    captured: dict[str, str] = {}

    class _FakeProvider:
        def set_model(self, model: str) -> None:
            captured["model"] = model

        async def recognize(self, *_a, **_k) -> OcrResult:
            return OcrResult(text="ok", lines=[], metadata={})

    fake = _FakeProvider()
    # Only gateway alias is registered — not the live VLM model id.
    svc._registry = MagicMock()

    def _get(name: str):
        if name == "qwen-vl-ocr-latest":
            return fake
        raise ValueError(f"OCR provider not found: {name}")

    svc._registry.get = MagicMock(side_effect=_get)
    svc._provider_name = "qwen-vl-ocr-latest"

    out = await svc.recognize(
        "synthetic.png",
        provider_name="qwen3.6-35b-a3b",
        model="qwen3.6-35b-a3b",
    )
    assert out.text == "ok"
    assert captured.get("model") == "qwen3.6-35b-a3b"
    assert any(
        call.args[0] == "qwen-vl-ocr-latest" for call in svc._registry.get.call_args_list
    )
