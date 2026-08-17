from app.ocr.interfaces import OcrLine, OcrProvider, OcrResult
import base64
import io
import logging
import mimetypes
import os
import random

import requests

from app.core.config import _DEFAULT_BANK_VLM_MODEL, _DEFAULT_OCR_ALIAS, settings


def _normalize_ocr_base_url(base_url: str) -> str:
    bu = (base_url or "").strip().rstrip("/")
    if not bu:
        bu = "https://www.dmxapi.cn"
    if not bu.endswith("/v1"):
        bu = f"{bu}/v1"
    return bu


class DeepSeekOcrProvider(OcrProvider):
    # DMXAPI OCR model (OpenAI-compatible)
    name = "DeepSeek-OCR"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if api_key is not None:
            self._api_key = api_key.strip()
        else:
            self._api_key = (
                os.getenv("VLM_API_KEY")
                or os.getenv("LLM_API_KEY")
                or ""
            )
        raw_base = (
            base_url
            if base_url is not None
            else (
                os.getenv("VLM_BASE_URL")
                or os.getenv("LLM_BASE_URL")
                or "https://www.dmxapi.cn"
            )
        )
        self._base_url = _normalize_ocr_base_url(raw_base)
        self._model = settings.vlm_model
        self._explicit_model: str | None = None  # set_model() override wins over env var
        self._prompt = (
            os.getenv("VLM_PROMPT") or "<image>\nFree OCR."
        )
        self._timeout = float(os.getenv("VLM_TIMEOUT") or "120")
        self._connect_timeout = float(os.getenv("VLM_CONNECT_TIMEOUT") or "10")
        self._read_timeout = float(
            os.getenv("VLM_READ_TIMEOUT") or str(self._timeout)
        )
        self._max_side = int(os.getenv("VLM_MAX_SIDE") or "0")
        self._image_format = (
            os.getenv("VLM_IMAGE_FORMAT") or "PNG"
        ).upper()
        self._jpeg_quality = int(os.getenv("VLM_JPEG_QUALITY") or "85")

        if not self._api_key:
            raise RuntimeError(
                "VLM OCR API key missing. Set VLM_API_KEY (or LLM_API_KEY)."
            )

    def set_model(self, model: str | None) -> None:
        """Explicitly set the model for the next recognize() call (takes priority over env var)."""
        self._explicit_model = model or None

    async def recognize(
        self,
        image_path: str,
        *,
        prompt_override: str | None = None,
        ocr_options: dict | None = None,
        image_options: dict | None = None,
    ) -> OcrResult:
        import asyncio

        logger = logging.getLogger(__name__)

        if not os.path.exists(image_path):
            raise RuntimeError(f"Image file not found: {image_path}")

        def _read_image_as_data_url(_img_opts: dict | None = None) -> str:
            from PIL import Image, ImageOps

            def _encode_bytes(raw_bytes: bytes, mime_type: str) -> str:
                encoded = base64.b64encode(raw_bytes).decode("ascii")
                return f"data:{mime_type};base64,{encoded}"

            # Per-call callers can pass image_options to override defaults:
            #   max_side  – cap the longer dimension (0 = no resize)
            #   format    – "JPEG" or "PNG" (overrides DEEPSEEK_OCR_IMAGE_FORMAT)
            #   quality   – JPEG quality 1-100 (overrides DEEPSEEK_OCR_JPEG_QUALITY)
            #
            # Bank-statement pages use max_side=1500 + format="JPEG" + quality=90
            # for a compact payload (~150-200 KB) that uploads quickly and gives the
            # VLM fewer image tokens to process.  The read_timeout (180 s) governs
            # the whole request so even larger payloads never hit the old
            # connect_timeout (20 s) mid-send on Windows.
            _img_opts = _img_opts or image_options or {}
            effective_max_side = (
                _img_opts["max_side"] if "max_side" in _img_opts else self._max_side
            )
            effective_format  = str(_img_opts.get("format",  self._image_format)).upper()
            effective_quality = int(_img_opts.get("quality", self._jpeg_quality))

            # Always open through PIL so we control format/compression.
            # EXIF orientation (common on phone photos) is applied so pixels match
            # how the user saw the image; cheques in portrait are readable top-to-bottom.
            # Sending the raw file bytes is dangerous: an uncompressed PNG at high
            # resolution can be several MB and exceed API payload limits.
            with Image.open(image_path) as img:
                img = ImageOps.exif_transpose(img)
                width, height = img.size
                max_dim = max(width, height)

                if effective_max_side > 0 and max_dim > effective_max_side:
                    scale = effective_max_side / max_dim
                    new_size = (int(width * scale), int(height * scale))
                    img = img.resize(new_size, Image.LANCZOS)
                    logger.info(
                        "[OCR] Resized image from %sx%s to %sx%s for upload",
                        width, height, new_size[0], new_size[1],
                    )
                elif effective_max_side == 0:
                    logger.info(
                        "[OCR] Full-resolution image %sx%s (resize disabled, format=%s q=%s)",
                        width, height, effective_format, effective_quality,
                    )

                output = io.BytesIO()
                if effective_format == "JPEG":
                    rgb_img = img.convert("RGB")
                    rgb_img.save(output, format="JPEG", quality=effective_quality, optimize=True)
                    return _encode_bytes(output.getvalue(), "image/jpeg")

                img.save(output, format="PNG", optimize=True)
                return _encode_bytes(output.getvalue(), "image/png")

        # Explicit model (set via set_model()) takes priority over Settings / env var.
        model_override = (
            self._explicit_model
            or (os.getenv("VLM_MODEL") or "").strip()
            or settings.vlm_model
        )
        self._explicit_model = None  # one-shot: reset after consuming
        prompt = prompt_override or self._prompt
        # Allow callers to override temperature via ocr_options (e.g. 0.0 for
        # deterministic structured extraction; default 0.1 for general OCR).
        options = ocr_options or {}
        temperature = options.get("temperature", 0.1)
        def _call_api(payload: dict) -> dict:
            # urllib3 applies connect_timeout to the ENTIRE send phase (headers + body),
            # not just the TCP handshake.  With connect_timeout=20 s and a ~1 MB JSON
            # payload, the socket times out mid-send before the body is fully uploaded.
            # Using a single read_timeout value (180 s) for the whole request lets the
            # body upload complete while still failing fast on dead servers (conn refused
            # returns almost instantly regardless of timeout).
            response = requests.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._read_timeout,
            )
            response.raise_for_status()
            return response.json()

        def _is_transport_error(exc: Exception) -> bool:
            msg = str(exc).lower()
            markers = (
                "connection aborted",
                "write operation timed out",
                "read timed out",
                "connect timeout",
                "protocolerror",
                "connection reset",
            )
            return any(m in msg for m in markers)

        # Build per-provider adaptive upload profiles. This keeps retry/fallback at the
        # correct layer: same provider first, progressively lighter payloads.
        base_opts = dict(image_options or {})
        base_format = str(base_opts.get("format", self._image_format)).upper()
        base_side = int(base_opts.get("max_side", self._max_side))

        profiles: list[dict] = []
        if base_format == "PNG":
            if base_side > 0:
                profiles.append({"max_side": base_side, "format": "PNG"})
                if base_side > 2200:
                    profiles.append({"max_side": 2200, "format": "PNG"})
                if base_side > 2000:
                    profiles.append({"max_side": 2000, "format": "PNG"})
            else:
                profiles.extend(
                    [
                        {"max_side": 2500, "format": "PNG"},
                        {"max_side": 2200, "format": "PNG"},
                        {"max_side": 2000, "format": "PNG"},
                    ]
                )
            # Last-resort transport profile.
            profiles.append({"max_side": 2200, "format": "JPEG", "quality": 90})
        else:
            profiles.append(base_opts or {"max_side": base_side, "format": base_format})

        # de-duplicate profiles while preserving order
        dedup_profiles: list[dict] = []
        seen: set[tuple] = set()
        for p in profiles:
            key = tuple(sorted(p.items()))
            if key not in seen:
                seen.add(key)
                dedup_profiles.append(p)
        profiles = dedup_profiles

        import json as _json
        _max_retries = 3
        _retryable = (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )
        response: dict | None = None
        last_exc: Exception | None = None
        for profile_idx, profile in enumerate(profiles, start=1):
            data_url = await asyncio.to_thread(_read_image_as_data_url, profile)
            payload = {
                "model": model_override,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
                "temperature": temperature,
            }
            if "max_tokens" in options:
                payload["max_tokens"] = int(options["max_tokens"])
            # Qwen3.x models run chain-of-thought (thinking) by default, generating
            # thousands of hidden reasoning tokens before the visible JSON output.
            # On dense bank-statement pages this pushes generation time past 180 s.
            # Pass enable_thinking=false to suppress the thinking chain and get
            # direct JSON output, keeping response times well under the timeout.
            if "enable_thinking" in options:
                payload["enable_thinking"] = bool(options["enable_thinking"])

            _payload_bytes = len(_json.dumps(payload).encode("utf-8"))
            logger.info(
                "[OCR] Sending request: model=%s, max_tokens=%s, profile=%d/%d, image_options=%s, payload_size=%.1f KB",
                model_override,
                options.get("max_tokens", "default"),
                profile_idx,
                len(profiles),
                profile,
                _payload_bytes / 1024,
            )

            for _attempt in range(_max_retries):
                try:
                    response = await asyncio.to_thread(_call_api, payload)
                    break
                except _retryable as exc:
                    last_exc = exc
                    if _attempt < _max_retries - 1:
                        # Exponential backoff + full jitter so that multiple parallel
                        # requests don't all retry simultaneously ("thundering herd").
                        _base = 2 ** _attempt        # 1 s, 2 s, 4 s
                        _delay = _base + random.uniform(0, _base)  # 1–2 s, 2–4 s, 4–8 s
                        logger.warning(
                            "[OCR] Transient connection error (attempt %d/%d), retrying in %.1fs: %s",
                            _attempt + 1, _max_retries, _delay, exc,
                        )
                        await asyncio.sleep(_delay)
                    else:
                        # Let the outer profile loop decide whether to downgrade.
                        pass
                except requests.exceptions.HTTPError as exc:
                    # Must be before RequestException — HTTPError subclasses RequestException.
                    resp = getattr(exc, "response", None)
                    code = resp.status_code if resp is not None else 0
                    if code in (429, 502, 503, 504) and _attempt < _max_retries - 1:
                        _base = 2 ** _attempt
                        _delay = _base + random.uniform(0, _base)
                        logger.warning(
                            "[OCR] Upstream HTTP %s (attempt %d/%d), retrying in %.1fs: %s",
                            code,
                            _attempt + 1,
                            _max_retries,
                            _delay,
                            exc,
                        )
                        await asyncio.sleep(_delay)
                        continue
                    body_snip = ""
                    if resp is not None and getattr(resp, "text", None):
                        body_snip = resp.text[:400].replace("\n", " ")
                    if code >= 400:
                        logger.warning(
                            "[OCR] HTTP %s from chat/completions. Body: %s",
                            code,
                            body_snip or "(empty)",
                        )
                    code_tag = f"OCR_HTTP_{code}" if code else "OCR_HTTP_ERROR"
                    raise RuntimeError(f"{code_tag}: DeepSeek OCR request failed: {exc}") from exc
                except requests.RequestException as exc:
                    # Other HTTP / request errors not handled above.
                    raise RuntimeError(f"OCR_REQUEST_ERROR: DeepSeek OCR request failed: {exc}") from exc

            if response is not None:
                break
            if last_exc and _is_transport_error(last_exc) and profile_idx < len(profiles):
                logger.warning(
                    "[OCR] Transport failure persisted for profile %d/%d; trying lighter profile.",
                    profile_idx,
                    len(profiles),
                )
                continue
            break

        if response is None:
            raise RuntimeError(f"OCR_REQUEST_ERROR: DeepSeek OCR request failed: {last_exc}")

        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError("OCR_NO_CHOICES: DeepSeek OCR returned no choices.")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not content:
            raise RuntimeError("OCR_EMPTY_CONTENT: DeepSeek OCR returned empty content.")

        lines: list[OcrLine] = []
        text_parts: list[str] = []
        for line in content.splitlines():
            line_text = line.strip()
            if not line_text:
                continue
            text_parts.append(line_text)
            lines.append(
                OcrLine(
                    text=line_text,
                    confidence=0.5,
                    bbox=[0, 0, 0, 0],
                    words=[],
                )
            )

        if not lines:
            logger.warning("DeepSeek OCR returned content without text lines.")

        return OcrResult(
            text=" ".join(text_parts),
            lines=lines,
            metadata={
                "source": self.name,
                "model": model_override,
                "base_url": self._base_url,
            },
        )


class OcrProviderRegistry:
    def __init__(self) -> None:
        provider = DeepSeekOcrProvider()
        ar_model = os.getenv("AR_OCR_MODEL", "")
        self._providers: dict[str, OcrProvider] = {
            "DeepSeek-OCR": provider,
        }
        self._providers[settings.ocr_provider] = provider
        self._providers[settings.document_layout_classify_model] = provider
        # Dynamically register any model name set via AR_OCR_MODEL env var
        if ar_model and ar_model not in self._providers:
            self._providers[ar_model] = provider
        # AP payables VLM — same resolution order as app.api.ocr (AP_VLM_MODEL → legacy → default)
        _ap_vlm = (
            os.getenv("AP_VLM_MODEL", "").strip()
            or os.getenv("AP_MULTI_RECEIPT_OCR_MODEL", "").strip()
            or (os.getenv("AP_VLM_DEFAULT") or "").strip()
            or _DEFAULT_OCR_ALIAS
        )
        if _ap_vlm not in self._providers:
            self._providers[_ap_vlm] = provider
        # Bank-statement row extraction — one model id (matches ocr.runtime BANK_VLM_MODEL).
        _bank_vlm = (os.getenv("BANK_VLM_MODEL") or "").strip() or _DEFAULT_BANK_VLM_MODEL
        if _bank_vlm not in self._providers:
            self._providers[_bank_vlm] = provider

        # Strategy B cross-VLM: model id and optional separate gateway (all from env).
        # Soft-fallback empty key/url fields to VLM via resolve_gateway.
        from app.core.gateway_settings import resolve_gateway, stored_gateway

        bank_stored = stored_gateway("bank_cross_vlm")
        bank_resolved = resolve_gateway("bank_cross_vlm")
        cross_vlm_model = (bank_stored.get("model") or "").strip()
        cross_key = (bank_stored.get("api_key") or "").strip()
        cross_base = (bank_stored.get("api_url") or "").strip()
        if cross_vlm_model:
            if cross_key or cross_base:
                if cross_vlm_model == _bank_vlm:
                    raise RuntimeError(
                        "BANK_CROSS_VLM_MODEL must differ from BANK_VLM_MODEL when "
                        "BANK_CROSS_VLM_API_KEY or BANK_CROSS_VLM_BASE_URL is set "
                        "(OCR registry uses model id as key)."
                    )
                eff_key = (bank_resolved.get("api_key") or "").strip()
                eff_base = (bank_resolved.get("api_url") or "").strip() or "https://www.dmxapi.cn"
                if not eff_key:
                    raise RuntimeError(
                        "BANK_CROSS_VLM_API_KEY or VLM_API_KEY is required "
                        "for cross-VLM when a separate gateway is configured."
                    )
                cross_provider = DeepSeekOcrProvider(api_key=eff_key, base_url=eff_base)
                self._providers[cross_vlm_model] = cross_provider
            elif cross_vlm_model not in self._providers:
                self._providers[cross_vlm_model] = provider

        # AP cross-VLM: optional dedicated gateway (key/url soft-fallback to VLM).
        ap_stored = stored_gateway("ap_cross_vlm")
        ap_resolved = resolve_gateway("ap_cross_vlm")
        ap_cross_model = (ap_stored.get("model") or "").strip()
        ap_cross_key = (ap_stored.get("api_key") or "").strip()
        ap_cross_base = (ap_stored.get("api_url") or "").strip()
        if ap_cross_model:
            if ap_cross_key or ap_cross_base:
                eff_key = (ap_resolved.get("api_key") or "").strip()
                eff_base = (ap_resolved.get("api_url") or "").strip() or "https://www.dmxapi.cn"
                if not eff_key:
                    raise RuntimeError(
                        "AP_CROSS_VLM_API_KEY or VLM_API_KEY is required "
                        "for AP cross-VLM when a separate gateway is configured."
                    )
                self._providers[ap_cross_model] = DeepSeekOcrProvider(
                    api_key=eff_key, base_url=eff_base
                )
            elif ap_cross_model not in self._providers:
                self._providers[ap_cross_model] = provider

    def get(self, name: str) -> OcrProvider:
        provider = self._providers.get(name)
        if not provider:
            raise ValueError(f"OCR provider not found: {name}")
        return provider
