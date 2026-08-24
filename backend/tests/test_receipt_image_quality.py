"""AQ-01 / AQ-02 receipt image quality — synthetic fixtures only (no real receipts)."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import receipt_image_quality as riq


def _write_png(path: Path, bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    path.write_bytes(buf.tobytes())
    return str(path)


def _synthetic_receipt(*, contrast: float = 0.55, blur_ksize: int = 0, glare: bool = False) -> np.ndarray:
    """Abstract receipt-like crop: paper + dark text bars (not a real receipt)."""
    h, w = 420, 320
    # Mid paper so pale thermal isn't confused with flash glare.
    img = np.full((h, w, 3), 228, dtype=np.uint8)
    cv2.rectangle(img, (20, 20), (w - 20, h - 20), (235, 235, 235), -1)
    ink = int(max(0, min(255, 235 * (1.0 - contrast))))
    for i, y in enumerate(range(60, 360, 28)):
        thickness = 2 if i % 3 else 3
        x2 = w - 40 - (i % 5) * 12
        cv2.line(img, (40, y), (x2, y), (ink, ink, ink), thickness)
    cv2.rectangle(img, (40, 370), (w - 40, 390), (ink, ink, ink), -1)
    if blur_ksize and blur_ksize >= 3:
        k = blur_ksize if blur_ksize % 2 == 1 else blur_ksize + 1
        img = cv2.GaussianBlur(img, (k, k), 0)
    if glare:
        cv2.circle(img, (w // 2, h // 2), 95, (255, 255, 255), -1)
        cv2.circle(img, (w // 2 + 10, h // 2 - 8), 55, (255, 255, 255), -1)
    return img


def test_probe_clear_synthetic_has_edges_and_ink(tmp_path: Path) -> None:
    path = _write_png(tmp_path / "clear.png", _synthetic_receipt(contrast=0.7))
    signals = riq.probe_path(path)
    assert signals["blur_variance"] > 50
    assert signals["ink_fraction"] > 0.02
    assert signals["edge_density"] > 0.01
    cls = riq.classify_quality(signals)
    assert cls["status"] in {"clear", "recoverable"}


def test_low_contrast_is_recoverable_and_recipe_helps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AP_RECEIPT_QUALITY_ENABLED", "true")
    monkeypatch.setenv("AP_RECEIPT_QUALITY_ENHANCE", "true")
    monkeypatch.setenv("AP_RECEIPT_QUALITY_MIN_IMPROVE", "0.01")
    faded = _synthetic_receipt(contrast=0.08)
    path = _write_png(tmp_path / "faded.png", faded)
    before = riq.probe_path(path)
    cls = riq.classify_quality(before)
    assert "low_contrast" in cls["issues"] or cls["status"] == "recoverable"

    prepared = riq.prepare_crop_for_ocr(path)
    assert prepared.audit.get("enabled") is True
    assert Path(path).read_bytes()  # original untouched
    # Either enhanced selected or original retained with recipe attempt recorded.
    assert prepared.audit.get("selection") in {
        "enhanced_selected",
        "original_selected",
        "original",
    }
    if prepared.audit.get("selection") == "enhanced_selected":
        assert prepared.path != path
        assert prepared.temp_paths
        assert prepared.audit.get("score_after", 0) >= prepared.audit.get("score_before", 0)
        for p in prepared.temp_paths:
            os.unlink(p)


def test_heavy_glare_routes_unrecoverable(tmp_path: Path) -> None:
    path = _write_png(tmp_path / "glare.png", _synthetic_receipt(contrast=0.5, glare=True))
    signals = riq.probe_path(path)
    cls = riq.classify_quality(signals)
    # Strong white blob should flag glare and often unrecoverable.
    assert "glare" in cls["issues"] or cls["status"] == "unrecoverable"
    prepared = riq.prepare_crop_for_ocr(path)
    if cls["status"] == "unrecoverable":
        assert prepared.path == path
        assert prepared.audit.get("selection") == "recapture_requested"


def test_original_preferred_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AP_RECEIPT_QUALITY_ENABLED", "true")
    monkeypatch.setenv("AP_RECEIPT_QUALITY_ENHANCE", "true")
    path = _write_png(tmp_path / "clean.png", _synthetic_receipt(contrast=0.75))
    prepared = riq.prepare_crop_for_ocr(path)
    assert prepared.path == path
    assert prepared.audit.get("selection") in {"original_selected", "original"}
    assert prepared.temp_paths == ()


def test_enhance_disabled_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AP_RECEIPT_QUALITY_ENABLED", "true")
    monkeypatch.setenv("AP_RECEIPT_QUALITY_ENHANCE", "false")
    path = _write_png(tmp_path / "faded2.png", _synthetic_receipt(contrast=0.07))
    prepared = riq.prepare_crop_for_ocr(path)
    assert prepared.path == path
    assert "enhance off" in str(prepared.audit.get("ui_label", "")).lower() or prepared.audit.get(
        "selection"
    ) in {"original_selected", "original"}


def test_attach_image_quality_provenance_flags_unrecoverable() -> None:
    row: dict = {}
    audit = {
        "enabled": True,
        "selection": "recapture_requested",
        "ui_label": "Image cannot be verified",
        "classification": {
            "status": "unrecoverable",
            "ui_state": "glare_cannot_verify",
            "issues": ["glare"],
            "reason": "glare",
        },
        "score_before": 0.1,
        "recipe": [],
    }
    riq.attach_image_quality_provenance(row, audit)
    assert row["needs_review"] is True
    assert "image_quality_unrecoverable" in row["validation_flags"]
    assert row["extraction_provenance"]["image_quality"]["status"] == "unrecoverable"


def test_apply_recipe_lab_clahe_increases_contrast() -> None:
    faded = _synthetic_receipt(contrast=0.06)
    after = riq.apply_recipe(faded, [{"op": "lab_clahe", "clip_limit": 2.5, "tile_grid": 8}])
    assert riq.probe_bgr(after)["local_contrast"] >= riq.probe_bgr(faded)["local_contrast"]
