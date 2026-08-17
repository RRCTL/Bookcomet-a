"""SEC-PRIV-002 / SEC-PRIV-005: bank prompt / fixture samples must stay obviously fictional.

Private client/merchant denylists must NOT live in this repository. This test only
enforces fictional account shapes and rejects a small set of generic non-fictional
patterns that should never appear as sample identifiers.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    BACKEND_ROOT / "app" / "bank_prompts",
    BACKEND_ROOT / "tests" / "test_bocom_v2_bf_opening.py",
    BACKEND_ROOT / "tests" / "test_boc_opening_row.py",
)

# Real-looking account shapes that are not the documented fictional placeholders.
# Lookbehind/ahead keep SCB NNN-N-NNNNNN-N from matching inside BOC NNN-NNN-N-NNNNNN-N.
HSBC_STYLE = re.compile(r"(?<![\d-])\d{3}-\d{6}-\d{3}(?![\d-])")
SCB_STYLE = re.compile(r"(?<![\d-])\d{3}-\d-\d{6}-\d(?![\d-])")
BOC_STYLE = re.compile(r"(?<![\d-])\d{3}-\d{3}-\d-\d{6}-\d(?![\d-])")
OCBC_STYLE = re.compile(r"(?<![\d-])\d{6}-\d{3}(?![\d-])")
BOCOM_STYLE = re.compile(r"(?<![\d])\d{14,15}(?![\d])")

ALLOWED_SCB_BOC = re.compile(
    r"^099-888-\d-\d{6}-\d$|^000-0-0{6}-\d$|^000-000-0-0{6}-0$"
)
ALLOWED_OCBC = re.compile(r"^000000-00[12]$")
ALLOWED_BOCOM = re.compile(r"^0{14}[12]$")

# Hard-fail if these well-known processor/merchant shapes re-enter prompt samples.
# Public brand shapes only — never private client names (those stay out of git entirely).
BLOCKED_PUBLIC_MERCHANT_SHAPES = (
    "STRIPE PAYMENTS",
    "PAYPAL HK",
    "ORACLE FOOD",
    "S.F. EXPRESS",
    "IFLARE",
    "FRQAFM",
    "NUTLINK",
    "LEKKER",
    "HONG KONG TV",
    "ESSENTMAGEFLIP",
)


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("*.py")))
    return files


@pytest.mark.parametrize("path", _iter_scan_files(), ids=lambda p: str(p.relative_to(BACKEND_ROOT)))
def test_bank_samples_use_fictional_identifiers(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    upper = text.upper()

    for name in BLOCKED_PUBLIC_MERCHANT_SHAPES:
        assert name not in upper, f"{path.name} still contains blocked merchant shape {name!r}"

    for match in HSBC_STYLE.findall(text):
        raise AssertionError(f"{path.name} has HSBC-style account {match}; use xxx-xxxxxx-xxx")

    for match in SCB_STYLE.findall(text):
        if not ALLOWED_SCB_BOC.match(match):
            raise AssertionError(f"{path.name} has SCB-style account {match}")

    for match in BOC_STYLE.findall(text):
        if not ALLOWED_SCB_BOC.match(match):
            raise AssertionError(f"{path.name} has BOC-style account {match}")

    for match in OCBC_STYLE.findall(text):
        if not ALLOWED_OCBC.match(match):
            raise AssertionError(f"{path.name} has OCBC-style account {match}")

    for match in BOCOM_STYLE.findall(text):
        if not ALLOWED_BOCOM.match(match):
            raise AssertionError(f"{path.name} has BOCOM-style account {match}")


def test_bank_prompt_pack_mentions_fictional_sample_tokens() -> None:
    """Smoke: HSBC prompt pack should demonstrate SAMPLE-* descriptors after scrub."""
    hsbc = (BACKEND_ROOT / "app" / "bank_prompts" / "hsbc.py").read_text(encoding="utf-8")
    upper = hsbc.upper()
    assert any(token in upper for token in ("SAMPLE PAYMENTS", "SAMPLE WALLET", "SAMPLE FOOD"))
    assert "STRIPE" not in upper
    assert "PAYPAL" not in upper
