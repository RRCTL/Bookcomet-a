"""Pool 2: content-addressed transaction/workflow artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from app.services.file_storage import write_bytes_atomic

logger = logging.getLogger(__name__)

_POOL2_ROOT = Path(os.getenv("TRANSACTIONS_DIR", "./transactions"))


def _category_folder(processing_mode: str) -> str:
    mode = (processing_mode or "AR").upper()
    mapping = {
        "AR": "ar",
        "AP": "ap",
        "BANK": "bank",
        "OTHER": "other",
    }
    return mapping.get(mode, mode.lower())


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash_json(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return content_hash(raw)


class Pool2Storage:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _POOL2_ROOT

    def node_output_path(
        self,
        company_id: str,
        run_id: str,
        node_id: str,
        content_id: str,
        *,
        ext: str = ".json",
    ) -> Path:
        return (
            self._root
            / company_id
            / "node_outputs"
            / run_id
            / node_id
            / f"{content_id}{ext}"
        )

    def save_node_output(
        self,
        company_id: str,
        run_id: str,
        node_id: str,
        payload: Any,
        *,
        ext: str = ".json",
    ) -> tuple[str, str]:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        cid = content_hash(raw)
        dest = self.node_output_path(company_id, run_id, node_id, cid, ext=ext)
        write_bytes_atomic(dest, raw)
        return cid, str(dest)

    def load_node_output(self, storage_path: str) -> dict[str, Any] | list[Any] | None:
        path = Path(storage_path)
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def final_package_path(
        self,
        company_id: str,
        processing_mode: str,
        run_id: str,
        package_id: str,
    ) -> Path:
        cat = _category_folder(processing_mode)
        return self._root / company_id / cat / run_id / f"{package_id}.json"

    def save_final_package(
        self,
        company_id: str,
        processing_mode: str,
        run_id: str,
        manifest: dict[str, Any],
    ) -> tuple[str, str]:
        raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        package_id = content_hash(raw)
        dest = self.final_package_path(company_id, processing_mode, run_id, package_id)
        write_bytes_atomic(dest, raw)
        logger.debug("[Pool2] Saved final package %s", dest)
        return package_id, str(dest)


pool2 = Pool2Storage()
