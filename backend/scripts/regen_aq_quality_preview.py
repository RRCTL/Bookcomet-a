#!/usr/bin/env python3
"""Regenerate synthetic AQ preview assets under frontend/public/mvdu-aq-preview (no real receipts)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("AP_RECEIPT_QUALITY_ENABLED", "true")
os.environ.setdefault("AP_RECEIPT_QUALITY_ENHANCE", "true")
os.environ.setdefault("AP_RECEIPT_QUALITY_MIN_IMPROVE", "0.01")

import cv2  # noqa: E402
from app.services import receipt_image_quality as riq  # noqa: E402
from tests.test_receipt_image_quality import _synthetic_receipt  # noqa: E402


def main() -> None:
    out_dir = ROOT / "frontend" / "public" / "mvdu-aq-preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*"):
        p.unlink()

    cases = [
        ("clean", _synthetic_receipt(contrast=0.75)),
        ("faded", _synthetic_receipt(contrast=0.07)),
        ("glare", _synthetic_receipt(contrast=0.5, glare=True)),
        ("blurry", _synthetic_receipt(contrast=0.55, blur_ksize=15)),
    ]
    results = []
    for name, img in cases:
        raw = out_dir / f"{name}_original.png"
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise RuntimeError(f"encode failed for {name}")
        raw.write_bytes(buf.tobytes())
        prep = riq.prepare_crop_for_ocr(str(raw))
        enhanced_rel = None
        if prep.path != str(raw) and Path(prep.path).exists():
            enh = out_dir / f"{name}_enhanced.png"
            enh.write_bytes(Path(prep.path).read_bytes())
            enhanced_rel = f"/mvdu-aq-preview/{enh.name}"
            for t in prep.temp_paths:
                try:
                    os.unlink(t)
                except OSError:
                    pass
        audit = prep.audit
        results.append(
            {
                "id": name,
                "label": name.capitalize(),
                "original_url": f"/mvdu-aq-preview/{raw.name}",
                "enhanced_url": enhanced_rel,
                "selection": audit.get("selection"),
                "ui_label": audit.get("ui_label"),
                "status": (audit.get("classification") or {}).get("status"),
                "ui_state": (audit.get("classification") or {}).get("ui_state"),
                "issues": (audit.get("classification") or {}).get("issues") or [],
                "reason": (audit.get("classification") or {}).get("reason"),
                "score_before": audit.get("score_before"),
                "score_after": audit.get("score_after"),
                "recipe": audit.get("recipe") or [],
                "quality_before": audit.get("quality_before"),
                "quality_after": audit.get("quality_after"),
            }
        )
        print(name, results[-1]["selection"], results[-1]["status"])

    (out_dir / "demo.json").write_text(json.dumps({"cases": results}, indent=2) + "\n")
    print("wrote", out_dir)


if __name__ == "__main__":
    main()
