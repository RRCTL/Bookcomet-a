"""Shared OpenAI-compatible gateway settings (VLM / LLM / enhance / cross-VLM).

Stored values come from dedicated env keys. Optional gateways soft-fallback empty
fields to VLM at resolve time; VLM itself has no fallback.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any
from urllib.parse import urlparse

from app.core.config import (
    _DEFAULT_AI_ENHANCE_REASONER_MODEL,
    _DEFAULT_DEPLOY_MODEL,
    _DEFAULT_VLM_MODEL,
    resolve_layout_classify_model,
    resolve_ocr_provider,
    resolve_settings_vlm_model,
    settings,
)

logger = logging.getLogger(__name__)


def normalize_openai_base_url(url: str) -> str:
    """Normalize an OpenAI-compatible API base (no vendor default).

    Empty stays empty. Non-empty bases are stripped and ensured to end with ``/v1``
    so callers can append ``/chat/completions`` or ``/models`` safely.
    """
    bu = (url or "").strip().rstrip("/")
    if not bu:
        return ""
    if not bu.endswith("/v1"):
        bu = f"{bu}/v1"
    return bu


def openai_chat_completions_url(base: str) -> str:
    """Build ``…/v1/chat/completions`` whether ``base`` already ends with ``/v1`` or not."""
    bu = (base or "").strip().rstrip("/")
    if not bu:
        raise ValueError("Gateway API URL is empty. Configure it in Settings → API.")
    if bu.endswith("/v1"):
        return f"{bu}/chat/completions"
    return f"{bu}/v1/chat/completions"


def openai_models_url(base: str) -> str:
    """Build ``…/v1/models`` whether ``base`` already ends with ``/v1`` or not."""
    bu = (base or "").strip().rstrip("/")
    if not bu:
        raise ValueError("Gateway API URL is empty. Configure it in Settings → API.")
    if bu.endswith("/v1"):
        return f"{bu}/models"
    return f"{bu}/v1/models"


def validate_gateway_url(url: str, *, purpose: str = "gateway") -> str:
    """Reject non-http(s) schemes and, outside APP_ENV=local, private/link-local targets.

    Local PC (APP_ENV=local) may probe loopback/LAN gateways. Non-local or when
    GATEWAY_BLOCK_PRIVATE_URLS=true blocks SSRF to internal addresses.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError(f"{purpose} URL is empty")
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"{purpose} URL must use http or https")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError(f"{purpose} URL is missing a host")

    force_block = os.getenv("GATEWAY_BLOCK_PRIVATE_URLS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    block_private = force_block or settings.app_env != "local"
    if not block_private:
        return cleaned

    lowered = host.lower().rstrip(".")
    if lowered in {"metadata.google.internal", "metadata"}:
        raise ValueError(f"{purpose} URL host is not allowed")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"{purpose} URL host could not be resolved") from exc

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"{purpose} URL resolves to a private or reserved address ({ip_str})"
            )
    return cleaned

GATEWAY_IDS = (
    "vlm",
    "llm",
    "ai_enhance",
    "bank_cross_vlm",
    "ap_cross_vlm",
)

# gateway_id -> (api_key_env, base_url_env, model_env)
GATEWAY_ENV_KEYS: dict[str, tuple[str, str, str]] = {
    "vlm": ("VLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL"),
    "llm": ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"),
    "ai_enhance": ("AI_ENHANCE_API_KEY", "AI_ENHANCE_BASE_URL", "AI_ENHANCE_MODEL"),
    "bank_cross_vlm": (
        "BANK_CROSS_VLM_API_KEY",
        "BANK_CROSS_VLM_BASE_URL",
        "BANK_CROSS_VLM_MODEL",
    ),
    "ap_cross_vlm": (
        "AP_CROSS_VLM_API_KEY",
        "AP_CROSS_VLM_BASE_URL",
        "AP_CROSS_VLM_MODEL",
    ),
}

OPTIONAL_GATEWAYS = frozenset(
    {"llm", "ai_enhance", "bank_cross_vlm", "ap_cross_vlm"}
)

_MASK = "***"


def _env_raw(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def stored_gateway(gateway_id: str) -> dict[str, str]:
    """Raw values from the process environment (what the Settings form shows)."""
    keys = GATEWAY_ENV_KEYS.get(gateway_id)
    if not keys:
        raise ValueError(f"Unknown gateway: {gateway_id}")
    key_env, url_env, model_env = keys
    return {
        "api_key": _env_raw(key_env),
        "api_url": _env_raw(url_env),
        "model": _env_raw(model_env),
    }


def resolve_gateway(gateway_id: str) -> dict[str, str]:
    """Per-field resolve: own value if set, else VLM for optional gateways."""
    stored = stored_gateway(gateway_id)
    if gateway_id == "vlm" or gateway_id not in OPTIONAL_GATEWAYS:
        return dict(stored)
    vlm = stored_gateway("vlm")
    return {
        "api_key": stored["api_key"] or vlm["api_key"],
        "api_url": stored["api_url"] or vlm["api_url"],
        "model": stored["model"] or vlm["model"],
    }


def mask_gateway(fields: dict[str, str]) -> dict[str, Any]:
    """Mask api_key for API responses."""
    key = (fields.get("api_key") or "").strip()
    return {
        "api_url": fields.get("api_url") or "",
        "model": fields.get("model") or "",
        "api_key": _MASK if key else "",
        "has_api_key": bool(key),
    }


def _sync_settings_from_env() -> None:
    """Refresh in-memory Settings fields from os.environ (legacy fallbacks kept)."""
    settings.vlm_api_key = (
        os.getenv("VLM_API_KEY") or os.getenv("LLM_API_KEY") or ""
    )
    settings.vlm_api_base = (
        os.getenv("VLM_BASE_URL") or os.getenv("LLM_BASE_URL") or ""
    )
    settings.vlm_model = resolve_settings_vlm_model()
    settings.deploy_model = (
        (os.getenv("LLM_MODEL") or os.getenv("DEPLOY_MODEL") or "").strip()
        or _DEFAULT_DEPLOY_MODEL
    )
    settings.ai_enhance_api_key = (
        os.getenv("AI_ENHANCE_API_KEY")
        or os.getenv("LLM_API_KEY")
        or os.getenv("VLM_API_KEY")
        or ""
    )
    settings.ai_enhance_api_base = (
        os.getenv("AI_ENHANCE_BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or os.getenv("VLM_BASE_URL")
        or ""
    )
    settings.ai_enhance_model = (
        (os.getenv("AI_ENHANCE_MODEL") or os.getenv("LLM_MODEL") or "").strip()
        or settings.deploy_model
    )
    settings.ai_enhance_reasoner_model = (
        (os.getenv("AI_ENHANCE_REASONER_MODEL") or "").strip()
        or _DEFAULT_AI_ENHANCE_REASONER_MODEL
    )
    settings.ai_enhance_use_reasoner = (
        os.getenv("AI_ENHANCE_USE_REASONER") or "false"
    ).lower() in ("true", "1", "yes")
    settings.ap_cross_vlm_model = os.getenv("AP_CROSS_VLM_MODEL", "").strip()
    settings.bank_vlm_model = resolve_settings_vlm_model(os.getenv("BANK_VLM_MODEL"))
    settings.ocr_provider = resolve_ocr_provider()
    settings.document_layout_classify_model = resolve_layout_classify_model()


def _apply_bank_cross_verify_flag(model: str) -> None:
    """Enable BANK_CROSS_VLM_VERIFY when model is set; clear when empty."""
    flag = "true" if (model or "").strip() else "false"
    os.environ["BANK_CROSS_VLM_VERIFY"] = flag


def apply_gateways(updates: dict[str, dict[str, str | None]]) -> dict[str, Any]:
    """Merge gateway updates into .env + os.environ, sync settings, refresh runtime.

    ``updates`` maps gateway_id -> {api_url?, model?, api_key?}.
    ``api_key`` of ``***`` keeps the existing secret. ``None`` skips the field.
    """
    from app.api.settings import _env_path, _read_env_file, _write_env_file

    path = _env_path()
    env_vars = _read_env_file(path)

    for gateway_id, fields in updates.items():
        keys = GATEWAY_ENV_KEYS.get(gateway_id)
        if not keys:
            raise ValueError(f"Unknown gateway: {gateway_id}")
        if not isinstance(fields, dict):
            raise ValueError(f"Invalid fields for gateway: {gateway_id}")
        key_env, url_env, model_env = keys
        if "api_key" in fields and fields["api_key"] is not None:
            raw_key = str(fields["api_key"])
            if raw_key != _MASK:
                env_vars[key_env] = raw_key.strip()
                os.environ[key_env] = raw_key.strip()
        if "api_url" in fields and fields["api_url"] is not None:
            url = str(fields["api_url"]).strip()
            if url:
                url = validate_gateway_url(url, purpose=f"{gateway_id} api_url")
            env_vars[url_env] = url
            os.environ[url_env] = url
        if "model" in fields and fields["model"] is not None:
            model = str(fields["model"]).strip()
            env_vars[model_env] = model
            os.environ[model_env] = model

    # Keep Bank Cross verify flag coherent when Bank Cross is edited via the API UI.
    if "bank_cross_vlm" in updates:
        bank_model = (os.environ.get("BANK_CROSS_VLM_MODEL") or "").strip()
        _apply_bank_cross_verify_flag(bank_model)
        env_vars["BANK_CROSS_VLM_VERIFY"] = os.environ["BANK_CROSS_VLM_VERIFY"]

    backup = _write_env_file(path, env_vars)
    _sync_settings_from_env()

    from app.ocr.runtime import refresh_ai_runtime

    refresh_ai_runtime()
    logger.info("Applied gateway settings for: %s", ", ".join(sorted(updates)))
    return {"backup_path": backup, "restart_required": False}


def probe_gateway(
    *,
    gateway_id: str,
    api_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """OpenAI-compatible connectivity probe using body values or resolve fallbacks."""
    import requests

    if gateway_id not in GATEWAY_ENV_KEYS:
        raise ValueError(f"Unknown gateway: {gateway_id}")

    resolved = resolve_gateway(gateway_id)
    url = (api_url if api_url is not None else "").strip() or resolved["api_url"]
    mdl = (model if model is not None else "").strip() or resolved["model"]
    key = (api_key if api_key is not None else "").strip()
    if not key or key == _MASK:
        key = resolved["api_key"]

    if not url:
        return {"ok": False, "message": "API URL is empty (no VLM fallback available)."}
    if not key:
        return {"ok": False, "message": "API key is empty (no VLM fallback available)."}
    if not mdl:
        return {"ok": False, "message": "Model is empty (no VLM fallback available)."}

    url = validate_gateway_url(url, purpose="probe")

    models_url = openai_models_url(url)
    chat_url = openai_chat_completions_url(url)

    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = requests.get(models_url, headers=headers, timeout=timeout)
        if resp.status_code < 400:
            return {
                "ok": True,
                "message": f"Connected ({resp.status_code} from /v1/models).",
                "gateway": gateway_id,
                "model": mdl,
            }
        # Some gateways deny /models; try a minimal chat completion.
        chat_resp = requests.post(
            chat_url,
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model": mdl,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=timeout,
        )
        if chat_resp.status_code < 400:
            return {
                "ok": True,
                "message": f"Connected ({chat_resp.status_code} from /v1/chat/completions).",
                "gateway": gateway_id,
                "model": mdl,
            }
        detail = (chat_resp.text or resp.text or "")[:300]
        return {
            "ok": False,
            "message": f"Probe failed ({chat_resp.status_code}): {detail}",
            "gateway": gateway_id,
            "model": mdl,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "message": f"Probe error: {exc}",
            "gateway": gateway_id,
            "model": mdl,
        }
