from app.services.re_vlm_hints import (
    build_rescan_prompt_block,
    normalize_expected_receipt_count,
    parse_expected_receipt_count,
    resolve_expected_receipt_count,
    sanitize_rescan_note,
    validate_rescan_reasons,
)


def test_validate_rescan_reasons_drops_unknown_and_dedupes():
    raw = ["wrong_amount", "unknown_chip", "wrong_amount", "missed_receipts"]
    assert validate_rescan_reasons(raw) == ["wrong_amount", "missed_receipts"]


def test_validate_rescan_reasons_caps_count():
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


def test_build_rescan_prompt_block_includes_expected_count():
    block = build_rescan_prompt_block(
        reasons=[],
        note=None,
        prior_summary=None,
        expected_receipt_count=10,
    )
    assert "Expected physical receipt count on this page: 10" in block


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


def test_parse_expected_receipt_count_any_n():
    assert parse_expected_receipt_count("Page has 9 taxi receipts") == 9
    assert parse_expected_receipt_count("expect 7 slips") == 7
    assert parse_expected_receipt_count("receipts: 10") == 10
    assert parse_expected_receipt_count("just taxi fees") is None
    assert parse_expected_receipt_count("1 receipt") is None  # need >= 2


def test_normalize_expected_receipt_count():
    assert normalize_expected_receipt_count(7) == 7
    assert normalize_expected_receipt_count("10") == 10
    assert normalize_expected_receipt_count(1) is None
    assert normalize_expected_receipt_count(99) is None
    assert normalize_expected_receipt_count(None) is None


def test_resolve_expected_receipt_count_hard_vs_soft():
    n, source, strength = resolve_expected_receipt_count(explicit=10, note="Page has 9 taxi receipts")
    assert n == 10
    assert source == "user_asserted"
    assert strength == "hard"

    n, source, strength = resolve_expected_receipt_count(explicit=None, note="Page has 7 receipts")
    assert n == 7
    assert source == "note_parsed"
    assert strength == "soft"

    n, source, strength = resolve_expected_receipt_count(explicit=None, note="unclear")
    assert n is None
    assert strength == "unknown"
