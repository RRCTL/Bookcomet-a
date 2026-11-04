import logging
import os
import random

from app.core.config import settings
from app.ocr.interfaces import OcrResult
from app.ocr.providers import OcrProviderRegistry


class OcrService:
    def __init__(self) -> None:
        self._registry = OcrProviderRegistry()
        # Use configured OCR provider (defaults set in Settings)
        provider = settings.ocr_provider
        # If user tries to use removed/legacy providers, fallback to configured OCR_PROVIDER
        if provider in ("paddle", "dummy", "easy", "tesseract"):
            logger = logging.getLogger(__name__)
            logger.warning(
                f"OCR provider '{provider}' has been removed. Using {settings.ocr_provider} instead."
            )
            provider = settings.ocr_provider
        self._provider_name = provider

    async def recognize(
        self,
        image_path: str,
        provider_name: str | None = None,
        model: str | None = None,
        prompt_override: str | None = None,
        ocr_options: dict | None = None,
        image_options: dict | None = None,
    ) -> OcrResult:
        import asyncio

        logger = logging.getLogger(__name__)
        selected_provider = provider_name or self._provider_name

        if selected_provider in ("paddle", "dummy", "easy", "tesseract"):
            logger.warning(
                "OCR provider '%s' has been removed. Using %s instead.",
                selected_provider,
                self._provider_name,
            )
            selected_provider = self._provider_name

        provider = self._registry.get(selected_provider)
        if model and hasattr(provider, "set_model"):
            provider.set_model(model)

        def _is_retryable_primary_error(exc: BaseException) -> bool:
            # Empty content is usually a model/routing miss, not a blip.
            # Retrying burns another VLM_READ_TIMEOUT (often minutes) with no gain.
            msg = str(exc).upper()
            return "OCR_HTTP_429" in msg or "TOO MANY REQUESTS" in msg

        def _provider_fingerprint(name: str, p: object) -> tuple[str, str, str]:
            return (
                (name or "").strip().lower(),
                str(getattr(p, "_base_url", "") or "").strip().lower(),
                str(getattr(p, "_model", "") or "").strip().lower(),
            )

        # Python 3 clears the name bound in `except ... as e` when the except block
        # ends; keep the failure on a variable for HSBC retry and fallback logic.
        primary_error: BaseException | None = None
        try:
            retry_cap = max(0, int(os.getenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "1")))
        except ValueError:
            retry_cap = 1
        for attempt in range(retry_cap + 1):
            try:
                return await provider.recognize(
                    image_path,
                    prompt_override=prompt_override,
                    ocr_options=ocr_options,
                    image_options=image_options,
                )
            except Exception as exc:
                primary_error = exc
                is_retryable = _is_retryable_primary_error(exc)
                if is_retryable and attempt < retry_cap:
                    base = float(2**attempt)
                    delay = base + random.uniform(0, base)
                    logger.warning(
                        "OCR provider '%s' retryable error (attempt %d/%d), retrying in %.1fs: %s",
                        selected_provider,
                        attempt + 1,
                        retry_cap + 1,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "OCR provider '%s' failed: %s",
                    selected_provider,
                    primary_error,
                    exc_info=True,
                )
                break

        if primary_error is None:
            raise RuntimeError("OCR recognize: primary path failed without exception")

        fallback_candidates = [
            name
            for name in ("DeepSeek-OCR", settings.ocr_provider)
            if name != selected_provider
        ]
        fallback_errors: list[str] = []

        for fallback_name in fallback_candidates:
            try:
                logger.info("Trying fallback OCR provider: %s", fallback_name)
                fallback_provider = self._registry.get(fallback_name)
                if _provider_fingerprint(selected_provider, provider) == _provider_fingerprint(
                    fallback_name, fallback_provider
                ):
                    logger.info(
                        "Skipping fallback OCR provider '%s' because endpoint/model matches primary.",
                        fallback_name,
                    )
                    continue
                # Do NOT forward the model override to the fallback — if the primary
                # model is rate-limited or unavailable, using the same model on the
                # fallback provider just fails again.  Let the fallback use its own
                # configured default model (from DEEPSEEK_OCR_MODEL env var).
                return await fallback_provider.recognize(
                    image_path,
                    prompt_override=prompt_override,
                    ocr_options=ocr_options,
                    image_options=image_options,
                )
            except Exception as fallback_error:
                fallback_errors.append(f"{fallback_name}: {fallback_error}")
                logger.error(
                    "Fallback OCR provider '%s' failed: %s",
                    fallback_name,
                    fallback_error,
                    exc_info=True,
                )

        error_details = "; ".join(fallback_errors) if fallback_errors else "no fallbacks attempted"
        raise RuntimeError(
            f"OCR failed with provider '{selected_provider}'. Fallbacks failed: {error_details}"
        )
