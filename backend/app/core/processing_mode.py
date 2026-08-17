"""Processing-mode helpers and legacy aliases."""
from __future__ import annotations

# Former Assets & Liabilities mode code.
_LEGACY_MODE_ALIASES = {
    "ASSET_LIA": "OTHER",
}


def normalize_processing_mode(mode: str | None, default: str = "AR") -> str:
    """Uppercase a mode code and map retired aliases (e.g. ASSET_LIA → OTHER)."""
    m = (mode or default).strip().upper() or default
    return _LEGACY_MODE_ALIASES.get(m, m)
