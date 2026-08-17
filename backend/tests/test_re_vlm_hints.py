from app.services.re_vlm_hints import (
    build_rescan_prompt_block,
    sanitize_rescan_note,
    validate_rescan_reasons,
)


def test_validate_rescan_reasons_drops_unknown_and_dedupes():
    raw = ["wrong_amount", "unknown_chip", "wrong_amount", "missed_receipts"]
    assert validate_rescan_reasons(raw) == ["wrong_amount", "missed_receipts"]


def test_validate_rescan_reasons_caps_count():
    raw = list(validate_rescan_reasons([]))  # noqa: sanity
    many = [
        "missed_receipts",
        "too_many_splits",
        "wrong_layout",
        "wrong_amount",
        "wrong_currency",
        "wrong_vendor",
        "wrong_date",
        "wrong_invoice_no",
        "gate_false_positive",
    ]
    assert len(validate_rescan_reasons(many)) == 8


def test_sanitize_rescan_note_strips_and_truncates():
    assert sanitize_rescan_note("  hello   world  ") == "hello world"
    assert len(sanitize_rescan_note("x" * 300)) == 200


def test_build_rescan_prompt_block_empty_when_no_input():
    assert build_rescan_prompt_block(reasons=[], note=None, prior_summary=None) == ""


def test_build_rescan_prompt_block_includes_markers_and_content():
    block = build_rescan_prompt_block(
        reasons=["wrong_amount", "missed_receipts"],
        note="Page has 3 JPY taxi receipts",
        prior_summary="gate=NON_TRANSACTIONAL; prior_rows=1",
    )
    assert "[USER RE-SCAN INSTRUCTIONS" in block
    assert "one-time" in block
    assert "Previous attempt context: gate=NON_TRANSACTIONAL" in block
    assert "incorrect amount" in block
    assert "missed one or more separate receipts" in block
    assert "Additional note: Page has 3 JPY taxi receipts" in block
    assert "rule memory" not in block.lower()


def test_structured_prompt_includes_rescan_supplement():
    from app.api.ocr import _build_ap_multi_receipt_structured_prompt

    block = build_rescan_prompt_block(
        reasons=["wrong_amount"],
        note="use JPY total",
        prior_summary=None,
    )
    prompt = _build_ap_multi_receipt_structured_prompt(
        ocr_text_hint="hint text",
        rescan_supplement=block,
    )
    assert "[USER RE-SCAN INSTRUCTIONS" in prompt
    assert "incorrect amount" in prompt
    assert "use JPY total" in prompt


def test_structured_prompt_cross_verify_and_rescan_both_append():
    from app.api.ocr import _build_ap_multi_receipt_structured_prompt

    block = build_rescan_prompt_block(reasons=["wrong_currency"], note=None, prior_summary=None)
    prompt = _build_ap_multi_receipt_structured_prompt(
        ocr_text_hint="hint",
        cross_verify=True,
        rescan_supplement=block,
    )
    assert "Verifier pass" in prompt
    assert "[USER RE-SCAN INSTRUCTIONS" in prompt
