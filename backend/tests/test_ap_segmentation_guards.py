from __future__ import annotations

import builtins
from pathlib import Path

from app.api import ocr


def test_multi_region_evidence_rejects_single_with_fragments() -> None:
    # One dominant receipt-like box + tiny fragments (typical false split).
    regions = [
        {"x": 80, "y": 80, "w": 820, "h": 1200},
        {"x": 90, "y": 1300, "w": 800, "h": 90},
        {"x": 120, "y": 1420, "w": 760, "h": 70},
    ]
    keep_multi, reason, stats = ocr._multi_region_evidence(
        regions,
        page_w=1000,
        page_h=1600,
    )
    assert keep_multi is False
    assert reason in {
        "weak_region_area",
        "dominant_region",
        "nested_fragments",
        "weak_inter_region_gap",
    }
    assert stats["region_count"] >= 2


def test_multi_region_evidence_keeps_true_multi() -> None:
    # Two substantial, clearly side-by-side regions (horizontal gap, similar area).
    regions = [
        {"x": 40, "y": 200, "w": 440, "h": 700},
        {"x": 520, "y": 220, "w": 440, "h": 680},
    ]
    keep_multi, reason, _stats = ocr._multi_region_evidence(
        regions,
        page_w=1000,
        page_h=1400,
    )
    assert keep_multi is True
    assert reason == "strong_multi_evidence"


def test_merge_regions_to_single_pads_and_clamps() -> None:
    merged = ocr._merge_regions_to_single(
        [{"x": 100, "y": 120, "w": 300, "h": 200}, {"x": 460, "y": 160, "w": 320, "h": 220}],
        page_w=900,
        page_h=700,
    )
    assert merged["x"] >= 0
    assert merged["y"] >= 0
    assert merged["w"] > 0
    assert merged["h"] > 0
    assert merged["x"] + merged["w"] <= 900
    assert merged["y"] + merged["h"] <= 700


def test_detect_receipt_regions_v2_falls_back_when_deps_missing(monkeypatch) -> None:
    sentinel = [{"x": 1, "y": 2, "w": 3, "h": 4}]
    monkeypatch.setattr(ocr, "_detect_receipt_regions", lambda _path: sentinel)

    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "cv2" or name.startswith("scipy"):
            raise ImportError("forced missing dependency")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = ocr._detect_receipt_regions_v2("dummy.png")
    assert out == sentinel


def test_pick_force_split_hypothesis_prefers_grid_when_no_count() -> None:
    v = [
        {"x": 0, "y": 0, "w": 100, "h": 300},
        {"x": 100, "y": 0, "w": 100, "h": 300},
        {"x": 200, "y": 0, "w": 100, "h": 300},
    ]
    h = [
        {"x": 0, "y": 0, "w": 300, "h": 100},
        {"x": 0, "y": 100, "w": 300, "h": 100},
        {"x": 0, "y": 200, "w": 300, "h": 100},
    ]
    picked = ocr._pick_force_split_hypothesis(v, h)
    assert len(picked) == 9


def test_pick_force_split_hypothesis_ranks_by_any_expected_count() -> None:
    v = [
        {"x": 0, "y": 0, "w": 100, "h": 200},
        {"x": 100, "y": 0, "w": 100, "h": 200},
    ]
    h2 = [
        {"x": 0, "y": 0, "w": 200, "h": 100},
        {"x": 0, "y": 100, "w": 200, "h": 100},
    ]
    picked = ocr._pick_force_split_hypothesis(v, h2, expected_count=4)
    assert len(picked) == 4
    picked7 = ocr._pick_force_split_hypothesis(v, h2, expected_count=7)
    # closest among {4 grid, 2 v, 2 h} → 4
    assert len(picked7) == 4


def test_force_split_tokyo_taxi_page_yields_nine_cells(tmp_path) -> None:
    """Regression: dense 3x3 composite must not collapse to 3 tall columns only."""
    import fitz

    pdf = Path(
        "/home/ubuntu/.cursor/projects/workspace/uploads/"
        "96c6a5a8-2d86-4bc7-ba23-1ff8372a61a4_2f94.pdf"
    )
    if not pdf.is_file():
        return
    doc = fitz.open(pdf)
    pix = doc[0].get_pixmap(dpi=150)
    img_path = tmp_path / "taxi.png"
    pix.save(str(img_path))
    regions = ocr._force_split_receipt_regions(str(img_path), expected_receipt_count=9)
    assert len(regions) == 9
    # Also works when note-style expected count is omitted: densest grid still preferred.
    regions_auto = ocr._force_split_receipt_regions(str(img_path))
    assert len(regions_auto) == 9
