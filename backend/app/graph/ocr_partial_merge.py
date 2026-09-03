"""Upsert mid-flight OCR pages so concurrent workers cannot drop earlier crops."""

from __future__ import annotations

from typing import Any


def ocr_page_upsert_key(page: dict[str, Any]) -> tuple[Any, ...]:
    rid = page.get("receipt_instance_id")
    if rid is not None and str(rid).strip():
        return ("id", str(rid))
    return ("idx", page.get("page"), page.get("receipt_index"))


def upsert_ocr_pages(
    existing: list[Any] | None,
    incoming: list[Any] | None,
) -> list[dict[str, Any]]:
    """Replace matching pages by receipt_instance_id, else (page, receipt_index)."""
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for src in (existing or [], incoming or []):
        if not isinstance(src, list):
            continue
        for page in src:
            if not isinstance(page, dict):
                continue
            key = ocr_page_upsert_key(page)
            if key not in by_key:
                order.append(key)
            by_key[key] = page
    return [by_key[key] for key in order]


def merge_partial_ocr_summary(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge a partial OCR snapshot into a running file/job summary."""
    if not isinstance(existing, dict):
        return dict(incoming)
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "pages":
            continue
        merged[key] = value
    merged["pages"] = upsert_ocr_pages(existing.get("pages"), incoming.get("pages"))
    return merged
