"""Runtime-owned OCR singletons and model settings.

API routes and bank parsers both need access to OCR providers. Keeping these
objects here avoids importing API modules from service code.
"""
from __future__ import annotations

import logging
import os

from app.core.config import resolve_settings_vlm_model, settings
from app.services.ai_post_processor import AiPostProcessor
from app.services.field_filtering import FieldFilteringPipeline
from app.services.ocr_service import OcrService

logger = logging.getLogger(__name__)


# Do not inject a vendor default into VLM_BASE_URL. An empty env must stay empty
# so Settings → API → VLM shows a blank API URL until the user configures one.
# OCR providers already fall back to LLM_BASE_URL (and their own callers) when unset.

# Single bank-statement VLM model for all banks (HSBC, BOC, BEA, SCB, ...).
# Per-bank behaviour differs by prompt in app/bank_prompts/, not by model id.
BANK_VLM_MODEL = resolve_settings_vlm_model(os.getenv("BANK_VLM_MODEL"))

ocr_service = OcrService()
filtering_pipeline = FieldFilteringPipeline()

_api_key = settings.ai_enhance_api_key
if _api_key:
    logger.info(
        "[AI] Initializing AI processor (model: %s, base: %s, reasoner: %s)",
        settings.ai_enhance_model,
        settings.ai_enhance_api_base,
        settings.ai_enhance_use_reasoner,
    )
else:
    logger.warning("[AI] AI enhancement API key not found in settings. AI enhancement will be disabled.")
    logger.warning(
        "[AI] To enable AI enhancement, set AI_ENHANCE_API_KEY (or LLM_API_KEY / VLM_API_KEY) "
        "in your .env file"
    )

ai_processor = AiPostProcessor()


def get_ocr_service() -> OcrService:
    return ocr_service


def _llm_deploy_creds() -> tuple[str, str]:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
    base_url = (
        os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or "https://www.dmxapi.cn"
    ).rstrip("/")
    return api_key, base_url


def refresh_ai_runtime() -> None:
    """Rebuild OCR registry + refresh import-time LLM/AI caches after live apply.

    Mutates existing singletons in place so modules that bound names at import
    (e.g. ``from app.ocr.runtime import ocr_service``) keep working.
    """
    global BANK_VLM_MODEL

    BANK_VLM_MODEL = resolve_settings_vlm_model(os.getenv("BANK_VLM_MODEL"))

    from app.ocr.providers import OcrProviderRegistry

    # Rebuild provider registry on the existing OcrService instance.
    ocr_service._registry = OcrProviderRegistry()
    provider = settings.ocr_provider
    if provider in ("paddle", "dummy", "easy", "tesseract"):
        provider = settings.ocr_provider
    ocr_service._provider_name = provider

    # Refresh AI enhance processor fields (clear lazy client).
    ai_processor.api_key = settings.ai_enhance_api_key
    ai_processor.api_base = settings.ai_enhance_api_base
    ai_processor.chat_model = settings.ai_enhance_model
    ai_processor.reasoner_model = (
        os.getenv("AI_ENHANCE_REASONER_MODEL") or ""
    ).strip() or settings.ai_enhance_reasoner_model
    ai_processor.use_reasoner = settings.ai_enhance_use_reasoner
    ai_processor._service = None

    api_key, base_url = _llm_deploy_creds()

    # DeployChatClient singleton
    try:
        from app.services import ai_chat_client as _chat_mod

        _chat_mod.deploy_chat_client.api_key = api_key
        _chat_mod.deploy_chat_client.base_url = base_url
        _chat_mod.deploy_chat_client.model = settings.deploy_model
    except Exception as exc:
        logger.warning("Failed to refresh deploy_chat_client: %s", exc)

    # Modules that cache LLM key/base at import time.
    for mod_path in (
        "app.api.ai_chat",
        "app.api.company_manual",
        "app.api.rule_memory",
        "app.api.other",
        "app.api.company_context",
        "app.services.company_manual_service",
    ):
        try:
            mod = __import__(mod_path, fromlist=["_DEPLOY_API_KEY"])
            if hasattr(mod, "_DEPLOY_API_KEY"):
                mod._DEPLOY_API_KEY = api_key
            if hasattr(mod, "_DEPLOY_BASE_URL"):
                mod._DEPLOY_BASE_URL = base_url
        except Exception as exc:
            logger.warning("Failed to refresh LLM cache in %s: %s", mod_path, exc)

    # AP / AR / BANK primary models + cross-VLM globals (ocr + tasks import path).
    ap_vlm = ""
    ar_ocr = ""
    try:
        from app.api import ocr as _ocr_api

        ap_vlm = _ocr_api.resolve_ap_vlm_model()
        ar_ocr = _ocr_api.resolve_ar_ocr_model()
        _ocr_api.AP_VLM_MODEL = ap_vlm
        _ocr_api.AP_MULTI_RECEIPT_OCR_MODEL = ap_vlm
        _ocr_api.AR_OCR_MODEL = ar_ocr
        _ocr_api.AP_CROSS_VLM_MODEL = os.getenv("AP_CROSS_VLM_MODEL", "").strip()
        _ocr_api.BANK_VLM_MODEL = BANK_VLM_MODEL
    except Exception as exc:
        logger.warning("Failed to refresh OCR API gateway globals: %s", exc)

    try:
        from app.api import tasks as _tasks_api

        if hasattr(_tasks_api, "AP_CROSS_VLM_MODEL"):
            _tasks_api.AP_CROSS_VLM_MODEL = os.getenv("AP_CROSS_VLM_MODEL", "").strip()
    except Exception as exc:
        logger.warning("Failed to refresh tasks AP_CROSS_VLM_MODEL: %s", exc)

    logger.info(
        "AI runtime refreshed (VLM_MODEL=%s AP_VLM=%s AR_OCR=%s BANK_VLM=%s deploy=%s ap_cross=%s)",
        settings.vlm_model,
        ap_vlm or "(unset)",
        ar_ocr or "(unset)",
        BANK_VLM_MODEL,
        settings.deploy_model,
        settings.ap_cross_vlm_model or "(unset)",
    )
