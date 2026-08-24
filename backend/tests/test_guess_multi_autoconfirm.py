"""Guess mode auto-confirms multi-receipt force-split (no needs_confirmation prompt)."""

from __future__ import annotations

import pytest

from app.api import ocr


@pytest.mark.asyncio
async def test_guess_autoconfirm_retries_with_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_multi(_path, **kwargs):  # type: ignore[no-untyped-def]
        confirmed = bool(kwargs.get("confirmed"))
        calls.append(confirmed)
        if not confirmed:
            return None
        return {"pages": [{"receipt_index": 1}], "ocr_job_outcome": "ok"}

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
    assert result is not None
    assert calls == [False, True]


@pytest.mark.asyncio
async def test_non_guess_still_asks_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_multi(_path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(bool(kwargs.get("confirmed")))
        return None

    monkeypatch.setattr(ocr, "_run_ap_multi_receipt_ocr_from_image", fake_multi)

    # Signal other than guess (should not occur often); still should ask.
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
    assert ask is True
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
