"""HTTP client boundary for AI chat completions."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


class DeployChatClient:
    """Small adapter around the OpenAI-compatible deployment API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
        self.base_url = (
            os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or os.getenv("VLM_BASE_URL") or ""
        ).rstrip("/")
        self.model = settings.deploy_model
        self.connect_timeout = float(os.getenv("DEPLOY_API_CONNECT_TIMEOUT", "15"))
        self.read_timeout = float(os.getenv("DEPLOY_API_READ_TIMEOUT", "300"))
        self.max_retries = int(
            os.getenv("LLM_MAX_RETRIES") or os.getenv("DEPLOY_API_MAX_RETRIES") or "2"
        )
        self.retry_backoff = float(
            os.getenv("LLM_RETRY_BACKOFF") or os.getenv("DEPLOY_API_RETRY_BACKOFF") or "1.0"
        )

    def complete(
        self, messages: list[dict[str, Any]], model: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        chosen_model = (model or "").strip() or self.model
        payload = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.3,
        }
        total_text_length = sum(len(str(msg.get("content", ""))) for msg in messages)
        payload_bytes = len(json.dumps(payload, ensure_ascii=False))
        logger.info(
            "[LLM] Calling chat completions: model=%s messages=%s text_length=%s payload_bytes=%s",
            chosen_model,
            len(messages),
            total_text_length,
            payload_bytes,
        )
        start_time = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                from app.core.gateway_settings import openai_chat_completions_url

                if not (self.base_url or "").strip():
                    raise ValueError(
                        "LLM API URL missing. Set LLM_BASE_URL (or VLM_BASE_URL) "
                        "in Settings → API."
                    )
                resp = requests.post(
                    openai_chat_completions_url(self.base_url),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=(self.connect_timeout, self.read_timeout),
                    verify=True,
                )
                resp.raise_for_status()
                data = resp.json()
                elapsed = time.perf_counter() - start_time
                logger.info("[LLM] Chat completions success: elapsed=%.2fs", elapsed)
                return data["choices"][0]["message"]["content"], data
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response else None
                if status_code in (401, 403):
                    logger.error("[LLM] Authentication failed (%s)", status_code)
                    raise ValueError("LLM API authentication failed. Check LLM_API_KEY.") from exc
                if status_code == 429:
                    logger.warning("[LLM] Rate limit exceeded (429)")
                    raise ValueError("LLM API rate limit exceeded. Try again later.") from exc
                if status_code == 400:
                    detail = exc.response.text[:500] if exc.response else str(exc)
                    logger.error("[LLM] Bad request (400): %s", detail)
                    raise ValueError("Invalid LLM request.") from exc
                last_error = exc
                if attempt <= self.max_retries:
                    sleep_for = self.retry_backoff * attempt
                    logger.warning(
                        "[LLM] HTTP %s (attempt %s/%s); retry in %.1fs",
                        status_code,
                        attempt,
                        self.max_retries + 1,
                        sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise ValueError(
                    f"LLM API request failed (status {status_code})."
                ) from exc
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = exc
                if attempt <= self.max_retries:
                    sleep_for = self.retry_backoff * attempt
                    logger.warning(
                        "[LLM] Network error (attempt %s/%s): %s; retry in %.1fs",
                        attempt,
                        self.max_retries + 1,
                        str(exc)[:200],
                        sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM chat completions failed without a captured error")


deploy_chat_client = DeployChatClient()
