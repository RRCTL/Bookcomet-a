"""
Load Bookcomet AI chat system prompts from `bookcomet_skills/<mode>/SKILL.md`.

Optional YAML frontmatter (--- ... ---) is stripped before returning body text.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_BOOKCOMET_SKILLS_DIR = Path(__file__).resolve().parent / "bookcomet_skills"

_FRONTMATTER = re.compile(r"^---\s*\r?\n.*?\r?\n---\s*\r?\n", re.DOTALL)


def _strip_frontmatter(raw: str) -> str:
    text = raw.lstrip("\ufeff").lstrip()
    if text.startswith("---"):
        m = _FRONTMATTER.match(raw.lstrip("\ufeff"))
        if m:
            return raw.lstrip("\ufeff")[m.end() :].strip()
        parts = raw.lstrip("\ufeff").split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return raw.strip()


@lru_cache(maxsize=32)
def load_skill(mode: str) -> str:
    """
    Return the system prompt body for the given mode (AR, AP, BANK, OTHER, RECON, REPORT).
    Falls back to AR if the skill file is missing.
    """
    from app.core.processing_mode import normalize_processing_mode

    key = normalize_processing_mode(mode, "AR")
    subdir = key.lower()
    path = _BOOKCOMET_SKILLS_DIR / subdir / "SKILL.md"
    if not path.is_file():
        logger.warning(
            "[BookcometSkill] Missing SKILL.md for mode=%s at %s; using ar/SKILL.md",
            mode,
            path,
        )
        path = _BOOKCOMET_SKILLS_DIR / "ar" / "SKILL.md"
    raw = path.read_text(encoding="utf-8")
    return _strip_frontmatter(raw)


def clear_skill_cache() -> None:
    """For tests or hot-reload tooling."""
    load_skill.cache_clear()
