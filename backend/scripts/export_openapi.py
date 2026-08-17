"""Export the FastAPI OpenAPI contract as JSON.

Usage:
    python scripts/export_openapi.py > openapi.json
    python scripts/export_openapi.py ../frontend/src/services/openapi.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


def main() -> None:
    payload = json.dumps(app.openapi(), ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")
        return
    print(payload)


if __name__ == "__main__":
    main()
