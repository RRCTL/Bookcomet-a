"""
AI enhancement chat client (OpenAI-compatible /v1/chat/completions).
Uses requests directly to avoid OpenAI SDK compatibility issues.
"""
import json
import logging
import os
import time
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AiEnhanceResult:
    """Structured extraction result from an AI enhancement call."""
    data: Dict[str, Any]
    raw: str
    model: str
    elapsed_time: float


class AiEnhanceClient:
    """
    OpenAI-compatible chat completions client for post-OCR / workflow extraction.

    Security: API key validation, HTTPS enforcement, input validation, retries.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ) -> None:
        self._api_key = (
            api_key
            or os.getenv("AI_ENHANCE_API_KEY")
            or os.getenv("LLM_API_KEY")
            or os.getenv("VLM_API_KEY")
            or ""
        )
        if not self._api_key:
            raise ValueError(
                "AI enhancement API key missing. Set AI_ENHANCE_API_KEY "
                "(or LLM_API_KEY / VLM_API_KEY) in .env file or pass as parameter."
            )

        if not self._api_key.startswith("sk-"):
            logger.warning("API Key format may be incorrect (should start with 'sk-')")

        resolved_base = (
            base_url
            or os.getenv("AI_ENHANCE_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or os.getenv("VLM_BASE_URL")
            or settings.ai_enhance_api_base
            or ""
        ).strip()
        if not resolved_base:
            raise ValueError(
                "AI enhancement API URL missing. Set AI_ENHANCE_BASE_URL "
                "(or VLM_BASE_URL) in Settings → API."
            )
        if not resolved_base.startswith("https://"):
            raise ValueError("Base URL must use HTTPS for security")
        self._base_url = resolved_base.rstrip("/")

        # Prefer live env (tests / runtime overrides) over import-time Settings snapshot.
        self._default_model = (
            default_model
            or os.getenv("AI_ENHANCE_MODEL")
            or os.getenv("LLM_MODEL")
            or settings.ai_enhance_model
            or ""
        ).strip()
        if not self._default_model:
            raise ValueError("AI enhancement model missing. Set AI_ENHANCE_MODEL in Settings → API.")

        logger.info(
            f"[LLM {self._default_model}] Service initialized (API Key: {'*' * 8}{self._api_key[-4:]}, "
            f"Base URL: {self._base_url}, Default Model: {self._default_model})"
        )

    def chat_completions(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        model_name = model or self._default_model
        self._validate_input(model_name, messages, temperature, max_tokens)

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)
        # Qwen3.x runs chain-of-thought by default; suppress for BANK post-OCR latency.
        model_lower = model_name.lower()
        if (
            "enable_thinking" not in payload
            and "qwen" in model_lower
            and "3" in model_lower
        ):
            payload["enable_thinking"] = False

        total_text_length = sum(len(str(msg.get("content", ""))) for msg in messages)
        logger.info(
            f"[LLM {model_name}] Calling API: model={model_name}, "
            f"messages={len(messages)}, text_length={total_text_length}"
        )

        start_time = time.perf_counter()
        max_retries = int(
            os.getenv("AI_ENHANCE_MAX_RETRIES") or os.getenv("LLM_MAX_RETRIES") or "2"
        )
        backoff_seconds = float(
            os.getenv("AI_ENHANCE_RETRY_BACKOFF") or os.getenv("LLM_RETRY_BACKOFF") or "1.0"
        )
        vlm_timeout = float(os.getenv("VLM_TIMEOUT") or "120")
        connect_timeout = float(os.getenv("VLM_CONNECT_TIMEOUT") or "10")
        read_timeout = float(os.getenv("VLM_READ_TIMEOUT") or str(vlm_timeout))

        for attempt in range(1, max_retries + 2):
            try:
                from app.core.gateway_settings import openai_chat_completions_url

                response = requests.post(
                    openai_chat_completions_url(self._base_url),
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload,
                    timeout=(connect_timeout, read_timeout),
                    verify=True
                )

                elapsed = time.perf_counter() - start_time
                response.raise_for_status()
                result = response.json()

                logger.info(
                    f"[LLM {model_name}] API success: model={model_name}, elapsed={elapsed:.2f}s"
                )

                return result

            except requests.exceptions.HTTPError as e:
                elapsed = time.perf_counter() - start_time
                status_code = e.response.status_code if e.response is not None else None

                if status_code == 401:
                    logger.error(f"[LLM {model_name}] API authentication failed (401)")
                    raise ValueError("API authentication failed. Please check your API key.")
                elif status_code == 429:
                    logger.warning(f"[LLM {model_name}] API rate limit exceeded (429)")
                    raise ValueError("API rate limit exceeded. Please try again later.")
                elif status_code == 400:
                    error_text = e.response.text[:500] if e.response is not None else str(e)
                    logger.error(f"[LLM {model_name}] API bad request (400): {error_text}")
                    raise ValueError("Invalid API request. Please check your input.")
                elif status_code in (500, 502, 503, 504) and attempt <= max_retries:
                    sleep_for = backoff_seconds * attempt
                    logger.warning(
                        f"[LLM {model_name}] API server error ({status_code}) "
                        f"(attempt {attempt}/{max_retries + 1}); retrying in {sleep_for:.1f}s..."
                    )
                    time.sleep(sleep_for)
                    continue
                else:
                    error_text = str(e)[:200]
                    logger.error(f"[LLM {model_name}] API error ({status_code}): {error_text}")
                    raise ValueError(f"API request failed (status {status_code}). Please try again later.")

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                elapsed = time.perf_counter() - start_time
                error_text = str(e)[:200]

                if attempt <= max_retries:
                    sleep_for = backoff_seconds * attempt
                    logger.warning(
                        f"[LLM {model_name}] Network timeout/error (attempt {attempt}/{max_retries + 1}): "
                        f"{error_text}. Retrying in {sleep_for:.1f}s..."
                    )
                    time.sleep(sleep_for)
                    continue

                if isinstance(e, requests.exceptions.Timeout):
                    logger.error(f"[LLM {model_name}] API request timeout after {elapsed:.2f}s")
                    raise ValueError("API request timeout. Please try again later.")

                logger.error(f"[LLM {model_name}] Network error after retries: {error_text}")
                raise ValueError("Network error occurred. Please check your connection.")

            except requests.exceptions.RequestException as e:
                elapsed = time.perf_counter() - start_time
                error_text = str(e)[:200]
                logger.error(f"[LLM {model_name}] Network error: {error_text}")
                raise ValueError("Network error occurred. Please check your connection.")

            except Exception as e:
                elapsed = time.perf_counter() - start_time
                error_text = str(e)[:200]
                logger.error(f"[LLM {model_name}] Unexpected error: {error_text}")
                raise ValueError("An unexpected error occurred. Please try again later.")

    def _validate_input(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: Optional[int]
    ) -> None:
        model_name = (model or "").strip()
        if not model_name or len(model_name) > 128:
            raise ValueError("Model name must be a non-empty string up to 128 characters")
        if not re.fullmatch(r"[\w.\-/+:]+", model_name):
            raise ValueError(f"Invalid model name format: {model_name!r}")

        if not isinstance(messages, list) or len(messages) == 0:
            raise ValueError("Messages must be a non-empty list")

        for msg in messages:
            if not isinstance(msg, dict):
                raise ValueError("Each message must be a dict")
            if "role" not in msg or "content" not in msg:
                raise ValueError("Each message must have 'role' and 'content' keys")

        total_length = sum(len(str(msg.get("content", ""))) for msg in messages)
        if total_length > 100000:
            raise ValueError(f"Total message content too long ({total_length} chars, max 100000)")

        if not (0.0 <= temperature <= 2.0):
            raise ValueError("Temperature must be between 0.0 and 2.0")

        if max_tokens is not None and (max_tokens < 1 or max_tokens > 4000):
            raise ValueError("max_tokens must be between 1 and 4000")

    def extract_fields_with_prompt(
        self,
        ocr_text: str,
        system_prompt: str,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None
    ) -> AiEnhanceResult:
        model_name = model or self._default_model

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ocr_text}
        ]

        start_time = time.perf_counter()
        response = self.chat_completions(
            messages=messages,
            model=model_name,
            temperature=0.1,
            max_tokens=max_tokens
        )
        elapsed = time.perf_counter() - start_time

        if not response.get("choices") or len(response["choices"]) == 0:
            raise ValueError("API returned no choices")

        choice = response["choices"][0]
        message = choice.get("message", {})
        content = message.get("content", "")

        reasoning_content = None
        if isinstance(message, dict) and "reasoning_content" in message:
            reasoning_content = message.get("reasoning_content")
        elif isinstance(choice, dict) and "reasoning_content" in choice:
            reasoning_content = choice.get("reasoning_content")
        elif "reasoning_content" in response:
            reasoning_content = response.get("reasoning_content")

        content = self._strip_reasoning(content)

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            extracted = self._extract_json_from_text(content)
            if extracted is not None:
                data = extracted
            else:
                logger.warning(f"[LLM {model_name}] Failed to parse JSON response, using raw text")
                data = {"raw_text": content}

        if isinstance(data, list):
            data = {"items": data}

        if reasoning_content:
            data["reasoning_content"] = reasoning_content
            logger.debug(f"[LLM {model_name}] Extracted reasoning content ({len(reasoning_content)} chars)")

        return AiEnhanceResult(
            data=data,
            raw=content,
            model=model_name,
            elapsed_time=elapsed
        )

    @staticmethod
    def _strip_reasoning(content: str) -> str:
        if "<think>" in content and "</think>" in content:
            content = re.sub(
                r"<think>.*?</think>",
                "",
                content,
                flags=re.DOTALL
            ).strip()

        return content.strip()

    @staticmethod
    def _extract_json_from_text(content: str) -> Optional[Dict[str, Any] | List[Any]]:
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.IGNORECASE)
        if code_block:
            candidate = code_block.group(1).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        obj_start = content.find("{")
        obj_end = content.rfind("}")
        arr_start = content.find("[")
        arr_end = content.rfind("]")

        candidates = []
        if obj_start != -1 and obj_end > obj_start:
            candidates.append(content[obj_start : obj_end + 1])
        if arr_start != -1 and arr_end > arr_start:
            candidates.append(content[arr_start : arr_end + 1])

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        return None
