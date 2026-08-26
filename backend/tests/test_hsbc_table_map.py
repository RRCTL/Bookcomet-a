"""Synthetic tests for HSBC Table Map P0/P1 (no real statement data or attachments)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.services.hsbc_table_map import (
    ExclusionBand,
    RowAnchor,
    assess_page_quality,
    build_exclusion_bands,
    build_hsbc_table_map,
    build_row_anchors,
    classify_hsbc_page,
    crop_window_bgr,
    enrich_prescan_with_table_map,
    filter_amounts_outside_exclusion,
    plan_transaction_windows,
    stable_row_id,
)


def _word(x0, y0, x1, y1, text, blk=0, ln=0, wi=0):
    """PyMuPDF-like word tuple (fictional layout only)."""
    return (float(x0), float(y0), float(x1), float(y1), str(text), blk, ln, wi)


def _synthetic_activity_words(*, include_totals: bool = True, include_portfolio: bool = True):
    """Build a fictional HSBC-like word layout (A4 ~595x842)."""
    w = 595.0
    words = []
    if include_portfolio:
        words.extend(
            [
                _word(40, 40, 120, 52, "Portfolio"),
                _word(125, 40, 200, 52, "Summary"),
                _word(400, 60, 460, 72, "1,000.00"),  # overview figure (not a txn)
            ]
        )
    # Section + column headers
    words.extend(
        [
            _word(40, 200, 280, 214, "HSBC"),
            _word(285, 200, 360, 214, "Business"),
            _word(365, 200, 420, 214, "Direct"),
            _word(425, 200, 460, 214, "HKD"),
            _word(465, 200, 520, 214, "Savings"),
            _word(80, 230, 120, 244, "Date"),
            _word(150, 230, 280, 244, "Transaction"),
            _word(285, 230, 340, 244, "Details"),
            _word(380, 230, 430, 244, "Deposit"),
            _word(450, 230, 520, 244, "Withdrawal"),
            _word(530, 230, 580, 244, "Balance"),
        ]
    )
    # Fictional transaction rows
    words.extend(
        [
            _word(50, 280, 70, 294, "6"),
            _word(75, 280, 110, 294, "Jan"),
            _word(150, 280, 280, 294, "SAMPLE-PAYEE-A"),
            _word(390, 280, 440, 294, "100.00"),
            _word(540, 280, 590, 294, "1100.00"),
            _word(50, 320, 70, 334, "7"),
            _word(75, 320, 110, 334, "Jan"),
            _word(150, 320, 280, 334, "SAMPLE-PAYEE-B"),
            _word(460, 320, 510, 334, "25.00"),
            _word(540, 320, 590, 334, "1075.00"),
        ]
    )
    if include_totals:
        words.extend(
            [
                _word(40, 400, 100, 414, "Total"),
                _word(105, 400, 160, 414, "Deposit"),
                _word(165, 400, 230, 414, "Amount"),
                _word(390, 400, 450, 414, "100.00"),
                _word(40, 430, 100, 444, "Total"),
                _word(105, 430, 180, 444, "Withdrawal"),
                _word(185, 430, 250, 444, "Amount"),
                _word(460, 430, 510, 444, "25.00"),
            ]
        )
    words.extend(
        [
            _word(40, 520, 120, 534, "Special"),
            _word(125, 520, 210, 534, "Privileges"),
            _word(40, 560, 90, 574, "Others"),
        ]
    )
    return words, w, 842.0


def test_totals_amount_never_enters_kept_anchors():
    words, _w, h = _synthetic_activity_words()
    bands = build_exclusion_bands(
        words,
        page_height=h,
        header_y=230.0,
        section_ys=[200.0],
    )
    assert any(b.reason == "section_totals" for b in bands)
    amounts = [
        {"y": 280.0, "col": "Cr", "amount": 100.0, "text": "100.00"},
        {"y": 320.0, "col": "Dr", "amount": 25.0, "text": "25.00"},
        {"y": 400.0, "col": "Cr", "amount": 100.0, "text": "100.00"},  # totals
        {"y": 430.0, "col": "Dr", "amount": 25.0, "text": "25.00"},  # totals
    ]
    kept, dropped = filter_amounts_outside_exclusion(amounts, bands)
    assert len(kept) == 2
    assert {round(a["y"], 1) for a in kept} == {280.0, 320.0}
    assert len(dropped) == 2
    assert all(d.get("_exclusion_reason") == "section_totals" for d in dropped)
    # Explicit acceptance: totals y never remain in kept anchors
    kept_ys = {a["y"] for a in kept}
    assert 400.0 not in kept_ys and 430.0 not in kept_ys


def test_portfolio_band_excludes_overview_amounts_above_header():
    words, _w, h = _synthetic_activity_words()
    bands = build_exclusion_bands(words, page_height=h, header_y=230.0, section_ys=[200.0])
    port = [b for b in bands if b.reason == "portfolio_overview"]
    assert port
    assert port[0].y0 == 0.0
    assert port[0].y1 >= 60.0
    amounts = [
        {"y": 60.0, "col": "Cr", "amount": 1000.0, "text": "1,000.00"},
        {"y": 280.0, "col": "Cr", "amount": 100.0, "text": "100.00"},
    ]
    kept, dropped = filter_amounts_outside_exclusion(amounts, bands)
    assert len(kept) == 1 and kept[0]["y"] == 280.0
    assert any(d["y"] == 60.0 for d in dropped)


def test_classify_mixed_and_portfolio_only():
    words, _w, h = _synthetic_activity_words()
    bands = build_exclusion_bands(words, page_height=h, header_y=230.0, section_ys=[200.0])
    mixed = classify_hsbc_page(
        no_table=False,
        words=words,
        sections=[{"y": 200.0, "header": "HSBC Business Direct HKD Savings"}],
        amounts=[{"y": 280.0, "col": "Cr", "amount": 100.0}],
        exclusion_bands=bands,
    )
    assert mixed == "mixed_activity_page"

    portfolio_only = classify_hsbc_page(
        no_table=True,
        page_text="Portfolio Summary account card",
        exclusion_bands=bands,
    )
    assert portfolio_only == "portfolio_only"

    legal = classify_hsbc_page(
        no_table=True,
        page_text="Special Privileges and legal notices",
        amounts=[],
    )
    assert legal == "legal_or_marketing"


def test_stable_row_id_invariant_across_retries():
    a = stable_row_id(
        page_number=3,
        section_id="hsbc-business-direct-hkd-savings",
        anchor_y=280.12,
        amount_side="Cr",
        printed_amount="100.00",
    )
    b = stable_row_id(
        page_number=3,
        section_id="hsbc-business-direct-hkd-savings",
        anchor_y=280.09,
        amount_side="Cr",
        printed_amount=100.0,
    )
    assert a == b
    assert len(a) == 64


def test_plan_windows_respects_max_and_does_not_cross_section_gap():
    anchors = []
    for i in range(12):
        anchors.append(
            RowAnchor(
                row_id=f"r{i}",
                y=100.0 + i * 18.0,
                amount_side="Cr",
                amount=10.0 + i,
                printed_text=f"{10 + i}.00",
                section_id="sec-a",
            )
        )
    # Large gap then another section
    for i in range(3):
        anchors.append(
            RowAnchor(
                row_id=f"s{i}",
                y=400.0 + i * 18.0,
                amount_side="Dr",
                amount=5.0,
                printed_text="5.00",
                section_id="sec-b",
            )
        )
    windows = plan_transaction_windows(
        anchors,
        page_height=842.0,
        target_rows=6,
        min_rows=4,
        max_rows=8,
    )
    assert windows
    assert all(len(w.expected_row_ids) <= 8 for w in windows)
    # Section change should start a new window rather than merging across gap
    section_ids = {tuple(sorted({a.section_id for a in anchors if a.row_id in w.expected_row_ids})) for w in windows}
    assert frozenset({"sec-a"}) in {frozenset(s) for s in section_ids}


def test_build_hsbc_table_map_normalized_boxes():
    bands = [
        ExclusionBand(y0=400.0, y1=460.0, reason="section_totals", source_text="Total Deposit Amount"),
    ]
    anchors = build_row_anchors(
        [{"y": 280.0, "col": "Cr", "amount": 100.0, "text": "100.00"}],
        page_number=2,
        sections=[{"y": 200.0, "header": "HSBC Business Direct HKD Savings"}],
    )
    tm = build_hsbc_table_map(
        page_number=2,
        page_width=595.0,
        page_height=842.0,
        classification="hkd_savings_activity",
        header_y=230.0,
        dep_hdr_x=400.0,
        wdw_hdr_x=480.0,
        bal_hdr_x=550.0,
        sections=[{"y": 200.0, "header": "HSBC Business Direct HKD Savings"}],
        exclusion_bands=bands,
        row_anchors=anchors,
        windows=plan_transaction_windows(anchors, page_height=842.0),
        quality={"render_profile": "digital_text_pdf_v1"},
        document_id="synthetic",
    )
    d = tm.to_dict()
    assert d["page_id"] == "document:synthetic:page:2"
    assert d["classification"] == "hkd_savings_activity"
    assert d["sections"]
    body = d["sections"][0]["table_body_box"]
    assert 0.0 <= body["x0"] < body["x1"] <= 1.0
    assert 0.0 <= body["y0"] < body["y1"] <= 1.0


def test_enrich_prescan_drops_totals_and_sets_classification(monkeypatch):
    monkeypatch.delenv("HSBC_SUMMARY_EXCLUSION", raising=False)
    words, w, h = _synthetic_activity_words()
    ps = {
        "amounts": [
            {"y": 280.0, "col": "Cr", "amount": 100.0, "text": "100.00"},
            {"y": 400.0, "col": "Cr", "amount": 100.0, "text": "100.00"},
        ],
        "balances": [],
        "sections": [{"y": 200.0, "header": "HSBC Business Direct HKD Savings"}],
        "date_labels": [{"y": 280.0, "label": "6 Jan"}],
        "header_y": 230.0,
        "dep_hdr_x": 405.0,
        "wdw_hdr_x": 485.0,
        "bal_hdr_x": 555.0,
        "page_height": h,
        "page_width": w,
        "no_table": False,
    }
    out = enrich_prescan_with_table_map(ps, words=words, page_number=5)
    assert len(out["amounts"]) == 1
    assert out["amounts"][0]["y"] == 280.0
    assert out["excluded_amounts"]
    assert out["classification"] == "mixed_activity_page"
    assert out["table_map"]["row_anchors"]
    assert out["windows"]


def test_enrich_respects_exclusion_feature_flag_off(monkeypatch):
    monkeypatch.setenv("HSBC_SUMMARY_EXCLUSION", "0")
    words, w, h = _synthetic_activity_words()
    ps = {
        "amounts": [
            {"y": 280.0, "col": "Cr", "amount": 100.0, "text": "100.00"},
            {"y": 400.0, "col": "Cr", "amount": 100.0, "text": "100.00"},
        ],
        "balances": [],
        "sections": [{"y": 200.0, "header": "HSBC Business Direct HKD Savings"}],
        "date_labels": [],
        "header_y": 230.0,
        "dep_hdr_x": 405.0,
        "wdw_hdr_x": 485.0,
        "bal_hdr_x": 555.0,
        "page_height": h,
        "page_width": w,
        "no_table": False,
    }
    out = enrich_prescan_with_table_map(ps, words=words, page_number=1)
    assert len(out["amounts"]) == 2
    assert out["excluded_amounts"] == []


def test_quality_and_crop_helpers_on_synthetic_image():
    img = np.full((400, 300, 3), 220, dtype=np.uint8)
    # Draw a few dark lines so Laplacian variance is non-trivial
    img[50:52, :] = 20
    img[150:152, :] = 20
    q = assess_page_quality(img, has_text_layer=False, description_fill_rate=0.1)
    assert q["render_profile"]
    assert "enhancement_recipe" in q
    crop = crop_window_bgr(
        img,
        y0_pt=40.0,
        y1_pt=90.0,
        render_scale=2.0,
        page_width_pt=150.0,
        header_y_pt=10.0,
    )
    assert crop.ndim == 3
    assert crop.shape[0] > 0 and crop.shape[1] > 0


def test_prescan_plus_enrich_on_fake_page():
    """End-to-end geometry path using a FakePage (no PDF attachment)."""
    from app.services.bank_statement_parser import BankStatementParser

    words, w, h = _synthetic_activity_words()
    # Place deposit/withdrawal amount tokens under column headers (x near hdr)
    # Rebuild with clearer column x for Deposit≈405, Withdrawal≈485
    words = [ww for ww in words if ww[4] not in {"100.00", "25.00", "1,000.00", "1100.00", "1075.00"}]
    words.extend(
        [
            _word(390, 280, 440, 294, "100.00"),  # deposit txn
            _word(460, 320, 510, 334, "25.00"),  # withdrawal txn
            _word(390, 400, 450, 414, "100.00"),  # total deposit amount
            _word(460, 430, 510, 444, "25.00"),  # total withdrawal amount
            _word(540, 280, 590, 294, "1100.00"),
            _word(540, 320, 590, 334, "1075.00"),
        ]
    )
    page = SimpleNamespace(
        rect=SimpleNamespace(width=w, height=h),
        get_text=lambda mode="text": words if mode == "words" else " ".join(x[4] for x in words),
    )
    ps = BankStatementParser._hsbc_prescan_amounts(page)
    assert ps["no_table"] is False
    raw_n = len(ps["amounts"])
    assert raw_n >= 2
    out = enrich_prescan_with_table_map(
        ps,
        words=ps.pop("_words", words),
        page_number=1,
        page_text=" ".join(x[4] for x in words),
    )
    kept_ys = {round(a["y"], 1) for a in out["amounts"]}
    assert 400.0 not in kept_ys
    assert 430.0 not in kept_ys
    assert out["table_map"]["classification"] in {
        "mixed_activity_page",
        "hkd_savings_activity",
        "activity_page",
    }
