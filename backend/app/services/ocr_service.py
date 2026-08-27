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

    @staticmethod
    def _resolved_fingerprint(p: object, *, model_override: str | None = None) -> tuple[str, str]:
        """Compare resolved endpoint + effective model — not human alias names."""
        base = str(getattr(p, "_base_url", "") or "").strip().lower().rstrip("/")
        explicit = getattr(p, "_explicit_model", None)
        effective = (
            (model_override or "").strip()
            or (str(explicit).strip() if explicit else "")
            or str(getattr(p, "_model", "") or "").strip()
        ).lower()
        return (base, effective)

    async def recognize(
        self,
        image_path: str,
        provider_name: str | None = None,
        model: str | None = None,
        prompt_override: str | None = None,
        ocr_options: dict | None = None,
        image_options: dict | None = None,
        *,
        allow_retry: bool = True,
        allow_fallback: bool = True,
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

        # Callers sometimes pass a VLM *model id* as provider_name (BANK path).
        # Model ids are not provider aliases — fall back to the configured OCR
        # gateway and keep the id as the model override.
        try:
            provider = self._registry.get(selected_provider)
        except ValueError:
            gateway = self._provider_name or settings.ocr_provider
            effective_model = (model or "").strip() or selected_provider
            logger.warning(
                "OCR provider %r is not registered; using gateway provider %r "
                "with model=%r",
                selected_provider,
                gateway,
                effective_model,
            )
            model = effective_model
            selected_provider = gateway
            provider = self._registry.get(selected_provider)
        if model and hasattr(provider, "set_model"):
            provider.set_model(model)

        def _is_retryable_primary_error(exc: BaseException) -> bool:
            # P0.1: empty content is a schema/upstream outcome — not a transport blip.
            # Retry only rate-limit / transient HTTP pressure.
            msg = str(exc).upper()
            return "OCR_HTTP_429" in msg or "TOO MANY REQUESTS" in msg

        # Python 3 clears the name bound in `except ... as e` when the except block
        # ends; keep the failure on a variable for HSBC retry and fallback logic.
        primary_error: BaseException | None = None
        if allow_retry:
            try:
                retry_cap = max(0, int(os.getenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "1")))
            except ValueError:
                retry_cap = 1
        else:
            retry_cap = 0
        primary_fp = self._resolved_fingerprint(provider, model_override=model)
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
                is_retryable = allow_retry and _is_retryable_primary_error(exc)
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

        if not allow_fallback:
            raise RuntimeError(
                f"OCR failed with provider '{selected_provider}' "
                f"(fallback disabled): {primary_error}"
            ) from primary_error

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
                fb_fp = self._resolved_fingerprint(fallback_provider, model_override=None)
                # Also treat "same endpoint + same settings model" as duplicate when
                # primary used an explicit model that matches the fallback default.
                settings_model = (os.getenv("VLM_MODEL") or settings.vlm_model or "").strip().lower()
                primary_effective = primary_fp[1] or settings_model
                fb_effective = fb_fp[1] or settings_model
                if primary_fp[0] == fb_fp[0] and primary_effective == fb_effective:
                    logger.info(
                        "Skipping fallback OCR provider '%s' because resolved "
                        "endpoint/model matches primary (fp=%s/%s).",
                        fallback_name,
                        primary_fp[0][-24:] if primary_fp[0] else "",
                        (primary_effective or "")[:32],
                    )
                    continue
                # Do NOT forward the model override to the fallback — if the primary
                # model is rate-limited or unavailable, using the same model on the
                # fallback provider just fails again.
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
