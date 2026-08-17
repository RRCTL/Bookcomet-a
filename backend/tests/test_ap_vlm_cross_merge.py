"""Unit tests for AP cross-VLM in-place merge."""
from app.services.ap_vlm_cross_merge import (
    cross_extraction_passes_confidence_gate,
    merge_ap_ai_enhanced_primary_with_cross,
)


def test_merge_overwrites_non_empty_cross_fields() -> None:
    primary = {
        "output_format": "tsv",
        "tsv_rows": [
            {
                "voucher_no": "A1",
                "amount": "10.00",
                "payee": "Old",
                "date": "2025-01-01",
                "currency": "HKD",
                "memo": "x",
                "confidence": "0.5",
            }
        ],
        "extraction_source": "ai_json",
    }
    cross = {
        "tsv_rows": [
            {
                "amount": "99.00",
                "payee": "New Vendor",
                "date": "",
                "confidence": "0.95",
            }
        ]
    }
    out = merge_ap_ai_enhanced_primary_with_cross(
        primary,
        cross,
        cross_model="test-model",
        policy="aggressive_overwrite",
    )
    row = out["tsv_rows"][0]
    assert row["amount"] == "99.00"
    assert row["payee"] == "New Vendor"
    assert row["date"] == "2025-01-01"
    assert row["voucher_no"] == "A1"
    assert "cross_vlm_merged" in str(out.get("extraction_source") or "")
    assert out.get("ap_cross_vlm_audit", {}).get("cross_model") == "test-model"


def test_merge_appends_extra_cross_rows() -> None:
    primary = {
        "tsv_rows": [{"amount": "1", "payee": "A"}],
    }
    cross = {"tsv_rows": [{"amount": "2"}, {"amount": "3", "payee": "B"}]}
    out = merge_ap_ai_enhanced_primary_with_cross(
        primary, cross, cross_model="m", policy="aggressive_overwrite"
    )
    assert len(out["tsv_rows"]) == 2
    assert out["tsv_rows"][0]["payee"] == "A"
    assert out["tsv_rows"][0]["amount"] == "2"
    assert out["tsv_rows"][1]["payee"] == "B"


def test_confidence_gate_zero_always_passes() -> None:
    cross = {"tsv_rows": [{"confidence": "0.1", "amount": "1"}]}
    assert cross_extraction_passes_confidence_gate(cross, 0.0) is True


def test_confidence_gate_blocks_low_cross() -> None:
    cross = {"tsv_rows": [{"confidence": "0.3", "amount": "1"}]}
    assert cross_extraction_passes_confidence_gate(cross, 0.5) is False
