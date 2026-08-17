from datetime import datetime, timezone
import hashlib
import json
from typing import Any


REQUIRED_STRING_FIELDS = ("action", "stage", "reason", "outcome")
REQUIRED_FIELDS = (
    "schema_version",
    "action",
    "stage",
    "reason",
    "outcome",
    "actor_user_id",
    "source",
    "trace_id",
    "confidence",
    "matched_by",
    "notes",
    "metadata",
    "timestamp",
    "content_hash",
)


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"decision_evidence.{field_name} is required")
    return normalized


def validate_decision_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize decision evidence payload."""
    for field_name in REQUIRED_FIELDS:
        if field_name not in payload:
            raise ValueError(f"decision_evidence missing field: {field_name}")

    for field_name in REQUIRED_STRING_FIELDS:
        raw_value = payload.get(field_name)
        if not isinstance(raw_value, str):
            raise ValueError(f"decision_evidence.{field_name} must be a string")
        payload[field_name] = _require_non_empty(raw_value, field_name)

    if payload.get("schema_version") != "v1":
        raise ValueError("decision_evidence.schema_version must be 'v1'")
    if not isinstance(payload.get("metadata"), dict):
        raise ValueError("decision_evidence.metadata must be an object")
    if not isinstance(payload.get("timestamp"), str):
        raise ValueError("decision_evidence.timestamp must be a string")
    if not isinstance(payload.get("content_hash"), str) or not payload.get("content_hash"):
        raise ValueError("decision_evidence.content_hash must be a non-empty string")

    return payload


def _compute_content_hash(payload: dict[str, Any]) -> str:
    # Deterministic hash excludes timestamp to avoid incidental differences.
    canonical = {k: v for k, v in payload.items() if k != "timestamp"}
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_decision_evidence(
    *,
    action: str,
    stage: str,
    reason: str,
    outcome: str,
    actor_user_id: str | None = None,
    source: str | None = None,
    trace_id: str | None = None,
    confidence: float | None = None,
    matched_by: str | None = None,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a consistent payload before persistence."""
    payload = {
        "schema_version": "v1",
        "action": action.strip(),
        "stage": stage.strip(),
        "reason": reason.strip(),
        "outcome": outcome.strip(),
        "actor_user_id": actor_user_id,
        "source": source,
        "trace_id": trace_id,
        "confidence": confidence,
        "matched_by": matched_by,
        "notes": notes,
        "metadata": metadata or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload["content_hash"] = _compute_content_hash(payload)
    return validate_decision_evidence(payload)
