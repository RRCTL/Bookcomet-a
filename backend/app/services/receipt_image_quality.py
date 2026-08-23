"""
AQ-01 / AQ-02: receipt image quality probes and minimal reversible OpenCV recipes.

Original crop files are never overwritten. Enhancement writes a new temp file.
Selection prefers the original unless a derivative clearly improves readability scores.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

import numpy as np

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except Exception:
        return default


def quality_enabled() -> bool:
    return _env_bool("AP_RECEIPT_QUALITY_ENABLED", True)


def enhance_enabled() -> bool:
    return quality_enabled() and _env_bool("AP_RECEIPT_QUALITY_ENHANCE", True)


def min_improve_delta() -> float:
    return max(0.0, _env_float("AP_RECEIPT_QUALITY_MIN_IMPROVE", 0.04))


@dataclass(frozen=True)
class PreparedCrop:
    """Path to feed primary OCR; may equal the original crop path."""

    path: str
    audit: dict[str, Any]
    temp_paths: tuple[str, ...] = ()


def _read_bgr(path: str):
    import cv2

    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        # Fallback for unusual paths
        img = cv2.imread(path, cv2.IMREAD_COLOR)
    return img


def _write_bgr(path: str, bgr: np.ndarray) -> None:
    import cv2

    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    buf.tofile(path)


def probe_bgr(bgr: np.ndarray) -> dict[str, float]:
    """AQ-01: objective quality signals on a BGR crop (normalized helpers 0..1 where noted)."""
    import cv2

    if bgr is None or bgr.size == 0:
        return {
            "blur_variance": 0.0,
            "local_contrast": 0.0,
            "glare_fraction": 1.0,
            "ink_fraction": 0.0,
            "edge_density": 0.0,
            "luminance_std": 0.0,
            "mean_luminance": 0.0,
            "noise_estimate": 0.0,
            "width": 0.0,
            "height": 0.0,
        }

    h, w = bgr.shape[:2]
    # Downsample large crops for stable, fast metrics.
    max_side = 640
    scale = min(1.0, max_side / float(max(h, w)))
    if scale < 1.0:
        bgr_s = cv2.resize(
            bgr,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        bgr_s = bgr

    gray = cv2.cvtColor(bgr_s, cv2.COLOR_BGR2GRAY)
    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    local_contrast = float(gray.std() / 255.0)
    mean_luminance = float(gray.mean() / 255.0)
    luminance_std = float(gray.std() / 255.0)
    glare_fraction = float(np.mean(gray >= 250))
    ink_fraction = float(np.mean(gray < 245))
    # Hotspot glare: bright flash vs broad paper background (large sigma).
    k = max(31, (min(gray.shape) // 3) | 1)
    if k % 2 == 0:
        k += 1
    local = cv2.GaussianBlur(gray, (k, k), 0)
    hotspot = (gray.astype(np.int16) - local.astype(np.int16)) >= 20
    hotspot &= gray >= 248
    glare_hotspot_fraction = float(np.mean(hotspot))
    edges = cv2.Canny(gray, 60, 150)
    edge_density = float(np.mean(edges > 0))
    # High-frequency residual as a cheap noise proxy.
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    noise_estimate = float(np.mean(np.abs(gray.astype(np.float32) - blur.astype(np.float32))) / 255.0)

    return {
        "blur_variance": round(blur_variance, 4),
        "local_contrast": round(local_contrast, 6),
        "glare_fraction": round(glare_fraction, 6),
        "glare_hotspot_fraction": round(glare_hotspot_fraction, 6),
        "ink_fraction": round(ink_fraction, 6),
        "edge_density": round(edge_density, 6),
        "luminance_std": round(luminance_std, 6),
        "mean_luminance": round(mean_luminance, 6),
        "noise_estimate": round(noise_estimate, 6),
        "width": float(w),
        "height": float(h),
    }


def probe_path(path: str) -> dict[str, float]:
    bgr = _read_bgr(path)
    if bgr is None:
        return probe_bgr(np.zeros((1, 1, 3), dtype=np.uint8))
    return probe_bgr(bgr)


def classify_quality(signals: Mapping[str, float]) -> dict[str, Any]:
    """
    Map signals → issues + routing status.

    status:
      clear | recoverable | unrecoverable
    ui_state:
      original_clear | faded_receipt | low_readability | blur_recapture |
      glare_cannot_verify | uneven_lighting | noisy
    """
    blur = float(signals.get("blur_variance", 0.0))
    contrast = float(signals.get("local_contrast", 0.0))
    glare_hot = float(signals.get("glare_hotspot_fraction", 0.0))
    glare_abs = float(signals.get("glare_fraction", 0.0))
    glare = max(glare_hot, glare_abs * 0.6 if glare_abs >= 0.12 else glare_hot)
    ink = float(signals.get("ink_fraction", 0.0))
    edge = float(signals.get("edge_density", 0.0))
    lum_std = float(signals.get("luminance_std", 0.0))
    noise = float(signals.get("noise_estimate", 0.0))

    issues: list[str] = []
    # Calibrated defaults for synthetic + typical phone scans; env overrides later via AQ-05.
    if glare_hot >= 0.05 or (glare_abs >= 0.12 and edge < 0.045):
        issues.append("glare")
    if blur < 35.0 and edge < 0.035:
        issues.append("blur")
    if contrast < 0.12 or ink < 0.04:
        issues.append("low_contrast")
    if lum_std >= 0.22 and contrast < 0.16:
        issues.append("uneven_illumination")
    if noise >= 0.045 and blur >= 40.0:
        issues.append("noise")

    # Unrecoverable: missing readable structure in critical ways.
    if "glare" in issues and (glare_hot >= 0.12 or glare_abs >= 0.18):
        return {
            "status": "unrecoverable",
            "ui_state": "glare_cannot_verify",
            "issues": issues,
            "reason": "Glare / overexposure covers too much of the crop; retake with indirect light.",
        }
    if "blur" in issues and blur < 18.0 and edge < 0.02:
        return {
            "status": "unrecoverable",
            "ui_state": "blur_recapture",
            "issues": issues,
            "reason": "Image is too blurry to verify totals or dates; recapture recommended.",
        }
    if ink < 0.015 and edge < 0.01:
        return {
            "status": "unrecoverable",
            "ui_state": "low_readability",
            "issues": issues or ["empty"],
            "reason": "Crop has almost no ink/edges; likely blank or cut-off.",
        }

    if not issues:
        return {
            "status": "clear",
            "ui_state": "original_clear",
            "issues": [],
            "reason": "Crop passes quality gates; prefer original.",
        }

    ui = "low_readability"
    if "low_contrast" in issues:
        ui = "faded_receipt"
    elif "uneven_illumination" in issues:
        ui = "uneven_lighting"
    elif "noise" in issues:
        ui = "noisy"
    elif "blur" in issues:
        ui = "low_readability"

    return {
        "status": "recoverable",
        "ui_state": ui,
        "issues": issues,
        "reason": "Recoverable quality issues detected; try minimal enhancement recipe.",
    }


def readability_score(signals: Mapping[str, float]) -> float:
    """Higher is better. Used for original-vs-enhanced selection (not absolute truth)."""
    blur = float(signals.get("blur_variance", 0.0))
    contrast = float(signals.get("local_contrast", 0.0))
    glare = float(signals.get("glare_hotspot_fraction", signals.get("glare_fraction", 0.0)))
    ink = float(signals.get("ink_fraction", 0.0))
    edge = float(signals.get("edge_density", 0.0))
    # Soft-cap blur contribution so ultra-noisy edges don't dominate.
    blur_term = min(blur, 400.0) / 400.0
    score = (
        0.30 * blur_term
        + 0.25 * min(contrast / 0.35, 1.0)
        + 0.20 * min(edge / 0.12, 1.0)
        + 0.15 * min(ink / 0.35, 1.0)
        - 0.35 * min(glare / 0.25, 1.0)
    )
    return round(float(score), 6)


def build_recipe(issues: list[str]) -> list[dict[str, Any]]:
    """AQ-02: minimal ops for detected issues only (no combinatorial explosion)."""
    recipe: list[dict[str, Any]] = []
    if "uneven_illumination" in issues:
        recipe.append({"op": "illumination_normalize"})
    if "low_contrast" in issues or "uneven_illumination" in issues:
        recipe.append({"op": "lab_clahe", "clip_limit": 2.0, "tile_grid": 8})
    if "noise" in issues:
        recipe.append({"op": "bilateral_denoise", "d": 5, "sigma_color": 40, "sigma_space": 40})
    if "blur" in issues:
        # Mild unsharp only — never aggressive sharpen that invents strokes.
        recipe.append({"op": "unsharp_mild", "amount": 0.35, "radius": 1.0})
    # Deskew is cheap and often helps phone scans even without explicit issue tag.
    if issues:
        recipe.insert(0, {"op": "deskew", "max_degrees": 12.0})
    return recipe


def _estimate_skew_degrees(gray: np.ndarray) -> float:
    import cv2

    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=60,
        minLineLength=max(20, gray.shape[1] // 8),
        maxLineGap=12,
    )
    if lines is None or len(lines) == 0:
        return 0.0
    angles: list[float] = []
    # OpenCV 4: (N,1,4); OpenCV 5: (N,4)
    flat = lines.reshape(-1, 4)
    for x1, y1, x2, y2 in flat:
        x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
        if abs(x2 - x1) < 1e-3:
            continue
        ang = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if abs(ang) <= 45:
            angles.append(ang)
    if not angles:
        return 0.0
    return float(np.median(angles))


def apply_recipe(bgr: np.ndarray, recipe: list[Mapping[str, Any]]) -> np.ndarray:
    import cv2

    out = bgr.copy()
    for step in recipe:
        op = str(step.get("op") or "")
        if op == "deskew":
            gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
            ang = _estimate_skew_degrees(gray)
            max_deg = float(step.get("max_degrees", 12.0))
            if 0.4 <= abs(ang) <= max_deg:
                h, w = out.shape[:2]
                m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
                out = cv2.warpAffine(
                    out,
                    m,
                    (w, h),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                )
        elif op == "illumination_normalize":
            lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            blur = cv2.GaussianBlur(l, (0, 0), sigmaX=max(5, min(l.shape) // 20))
            # Avoid divide-by-zero; keep mid-gray background.
            norm = cv2.divide(l, blur, scale=128)
            lab2 = cv2.merge([norm, a, b])
            out = cv2.cvtColor(lab2, cv2.COLOR_LAB2BGR)
        elif op == "lab_clahe":
            clip = float(step.get("clip_limit", 2.0))
            tile = int(step.get("tile_grid", 8))
            lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
            l2 = clahe.apply(l)
            out = cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
        elif op == "bilateral_denoise":
            out = cv2.bilateralFilter(
                out,
                d=int(step.get("d", 5)),
                sigmaColor=float(step.get("sigma_color", 40)),
                sigmaSpace=float(step.get("sigma_space", 40)),
            )
        elif op == "unsharp_mild":
            amount = float(step.get("amount", 0.35))
            radius = float(step.get("radius", 1.0))
            blur = cv2.GaussianBlur(out, (0, 0), sigmaX=radius)
            out = cv2.addWeighted(out, 1.0 + amount, blur, -amount, 0)
    return out


def prepare_crop_for_ocr(crop_path: str) -> PreparedCrop:
    """
    AQ-01 probe + optional AQ-02 recipe. Never mutates crop_path.

    Returns PreparedCrop with audit suitable for Table Review provenance.
    """
    if not quality_enabled():
        return PreparedCrop(
            path=crop_path,
            audit={"enabled": False, "selection": "disabled"},
        )

    original = _read_bgr(crop_path)
    if original is None:
        return PreparedCrop(
            path=crop_path,
            audit={
                "enabled": True,
                "selection": "original",
                "error": "unreadable_crop",
                "status": "unrecoverable",
                "ui_state": "low_readability",
            },
        )

    before = probe_bgr(original)
    classification = classify_quality(before)
    before_score = readability_score(before)
    audit: dict[str, Any] = {
        "enabled": True,
        "quality_before": before,
        "classification": classification,
        "score_before": before_score,
        "selection": "original",
        "recipe": [],
        "selected_for_primary_ocr": True,
        "original_path_basename": os.path.basename(crop_path),
    }

    if classification["status"] == "clear" or not enhance_enabled():
        audit["selection"] = "original_selected"
        audit["ui_label"] = "Original · clear" if classification["status"] == "clear" else "Original · enhance off"
        return PreparedCrop(path=crop_path, audit=audit)

    if classification["status"] == "unrecoverable":
        audit["selection"] = "recapture_requested"
        audit["ui_label"] = "Image cannot be verified"
        # Still OCR original (table-first) but mark for review — do not invent via enhancement.
        return PreparedCrop(path=crop_path, audit=audit)

    recipe = build_recipe(list(classification.get("issues") or []))
    if not recipe:
        audit["selection"] = "original_selected"
        audit["ui_label"] = "Original · no recipe"
        return PreparedCrop(path=crop_path, audit=audit)

    try:
        enhanced = apply_recipe(original, recipe)
        after = probe_bgr(enhanced)
        after_score = readability_score(after)
        audit["quality_after"] = after
        audit["score_after"] = after_score
        audit["recipe"] = recipe

        # Original priority: need clear improvement and not worse glare.
        glare_worse = float(
            after.get("glare_hotspot_fraction", after.get("glare_fraction", 0))
        ) > float(
            before.get("glare_hotspot_fraction", before.get("glare_fraction", 0))
        ) + 0.02
        if (after_score >= before_score + min_improve_delta()) and not glare_worse:
            fd, out_path = tempfile.mkstemp(suffix=".receipt-aq.png")
            os.close(fd)
            _write_bgr(out_path, enhanced)
            audit["selection"] = "enhanced_selected"
            audit["ui_label"] = "Auto-enhanced · view original"
            audit["variant_id"] = "rv_enhanced"
            audit["parent_variant_id"] = "rv_original"
            return PreparedCrop(path=out_path, audit=audit, temp_paths=(out_path,))

        audit["selection"] = "original_selected"
        audit["ui_label"] = "Original · enhance not better"
        audit["enhance_rejected_reason"] = (
            "glare_worse" if glare_worse else "insufficient_score_gain"
        )
        return PreparedCrop(path=crop_path, audit=audit)
    except Exception as exc:
        logger.warning("[AQ] enhancement failed for %s: %s", crop_path, exc)
        audit["selection"] = "original_selected"
        audit["ui_label"] = "Original · enhance failed"
        audit["error"] = str(exc)[:500]
        return PreparedCrop(path=crop_path, audit=audit)


def attach_image_quality_provenance(
    row: MutableMapping[str, Any],
    audit: Mapping[str, Any] | None,
) -> MutableMapping[str, Any]:
    """Attach quality audit under extraction_provenance for Table Review."""
    if not audit:
        return row
    prev = row.get("extraction_provenance")
    prov: dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}
    # Store a compact, JSON-safe copy (no absolute paths).
    safe = {
        "enabled": audit.get("enabled"),
        "selection": audit.get("selection"),
        "ui_label": audit.get("ui_label"),
        "ui_state": (audit.get("classification") or {}).get("ui_state")
        if isinstance(audit.get("classification"), dict)
        else audit.get("ui_state"),
        "status": (audit.get("classification") or {}).get("status")
        if isinstance(audit.get("classification"), dict)
        else audit.get("status"),
        "reason": (audit.get("classification") or {}).get("reason")
        if isinstance(audit.get("classification"), dict)
        else audit.get("reason"),
        "issues": (audit.get("classification") or {}).get("issues")
        if isinstance(audit.get("classification"), dict)
        else [],
        "score_before": audit.get("score_before"),
        "score_after": audit.get("score_after"),
        "recipe": audit.get("recipe") or [],
        "quality_before": audit.get("quality_before"),
        "quality_after": audit.get("quality_after"),
    }
    if safe.get("status") == "unrecoverable" or audit.get("selection") == "recapture_requested":
        row["needs_review"] = True
        flags = list(row.get("validation_flags") or []) if isinstance(row.get("validation_flags"), list) else []
        if "image_quality_unrecoverable" not in flags:
            flags.append("image_quality_unrecoverable")
        row["validation_flags"] = flags
    prov["image_quality"] = safe
    row["extraction_provenance"] = prov
    return row


def probe_page(path: str) -> dict[str, Any]:
    """Optional page-level probe for multi-receipt parent images."""
    signals = probe_path(path)
    classification = classify_quality(signals)
    return {
        "quality": signals,
        "classification": classification,
        "score": readability_score(signals),
    }
