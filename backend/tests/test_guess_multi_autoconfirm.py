"""Guess mode: one Detect call, never OpenCV force-split, no confirmation prompt."""

from __future__ import annotations

import pytest

from app.api import ocr


@pytest.mark.asyncio
async def test_guess_does_not_retry_opencv_force_split(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_multi(_path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(bool(kwargs.get("confirmed")))
        return None

    monkeypatch.setattr(ocr, "_run_ap_multi_receipt_ocr_from_image", fake_multi)

    result, ask = await ocr._run_ap_multi_with_guess_autoconfirm(
        "dummy.png",
        trace_id="t",
        filename="f.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        multi_receipt_confirmed=False,
        ap_receipt_signal="guess",
    )
    assert ask is False
    assert result is None
    assert calls == [False]


@pytest.mark.asyncio
async def test_non_guess_does_not_ask_opencv_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_multi(_path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(bool(kwargs.get("confirmed")))
        return None

    monkeypatch.setattr(ocr, "_run_ap_multi_receipt_ocr_from_image", fake_multi)

    result, ask = await ocr._run_ap_multi_with_guess_autoconfirm(
        "dummy.png",
        trace_id="t",
        filename="f.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        multi_receipt_confirmed=False,
        ap_receipt_signal="single_per_page",
    )
    assert result is None
    assert ask is False
    assert calls == [False]


@pytest.mark.asyncio
async def test_already_confirmed_no_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_multi(_path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(bool(kwargs.get("confirmed")))
        return None

    monkeypatch.setattr(ocr, "_run_ap_multi_receipt_ocr_from_image", fake_multi)

    result, ask = await ocr._run_ap_multi_with_guess_autoconfirm(
        "dummy.png",
        trace_id="t",
        filename="f.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        multi_receipt_confirmed=True,
        ap_receipt_signal="multi_per_page",
    )
    assert result is None
    assert ask is False
    assert calls == [True]
