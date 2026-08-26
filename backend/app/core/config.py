import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Load backend/.env for keys not already set in the process environment.
# Tests and CI may set variables before import; they take precedence over the file.
_backend_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_backend_env_path, override=False)

# Default model IDs — must be overridden via Settings → API for production BANK/VLM.
# Fail-closed helpers refuse empty runtime config instead of silently swapping gateways.
_DEFAULT_OCR_ALIAS = "qwen-vl-ocr-latest"
_DEFAULT_DEPLOY_MODEL = "qwen3.5-plus-2026-02-15"
_DEFAULT_BANK_VLM_MODEL = "qwen-vl-ocr-latest"
_DEFAULT_VLM_MODEL = "qwen-vl-ocr-latest"
_DEFAULT_LAYOUT_CLASSIFY_MODEL = "qwen3-vl-plus"
_DEFAULT_AI_ENHANCE_REASONER_MODEL = "qwen3.5-plus-2026-02-15"


def resolve_bank_vlm_model(*, fail_closed: bool = False) -> str:
    """BANK extraction model: Settings → API VLM only (optional BANK_VLM_MODEL pin).

    When ``fail_closed`` is True (Slice 3), empty VLM_MODEL / BANK_VLM_MODEL raises
    instead of returning a hardcoded default that would silently change quality.
    """
    explicit = (os.getenv("BANK_VLM_MODEL") or "").strip()
    if explicit:
        return explicit
    vlm = (os.getenv("VLM_MODEL") or "").strip()
    if vlm:
        return vlm
    if fail_closed:
        raise ValueError(
            "BANK VLM is not configured. Set Settings → API → VLM model "
            "(VLM_MODEL), or optional BANK_VLM_MODEL override."
        )
    return _DEFAULT_VLM_MODEL


def require_bank_vlm_settings() -> dict[str, str]:
    """Fail-closed gate before BANK / Cross-VLM calls.

    Requires VLM_MODEL (or BANK_VLM_MODEL) and VLM_API_KEY + VLM_BASE_URL.
    Does not inject vendor URLs or model IDs.
    """
    model = resolve_bank_vlm_model(fail_closed=True)
    api_key = (os.getenv("VLM_API_KEY") or "").strip()
    base_url = (os.getenv("VLM_BASE_URL") or "").strip()
    if not api_key:
        raise ValueError(
            "BANK VLM API key is not configured. Set Settings → API → VLM API key."
        )
    if not base_url:
        raise ValueError(
            "BANK VLM API URL is not configured. Set Settings → API → VLM API URL."
        )
    return {"model": model, "api_key": api_key, "api_url": base_url}


def require_bank_cross_vlm_settings() -> dict[str, str] | None:
    """If Cross-VLM is enabled, require explicit model + credentials (no silent default).

    Returns None when Cross-VLM is disabled (empty BANK_CROSS_VLM_MODEL and verify off).
    """
    model = (os.getenv("BANK_CROSS_VLM_MODEL") or "").strip()
    verify = (os.getenv("BANK_CROSS_VLM_VERIFY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not model and not verify:
        return None
    if not model:
        raise ValueError(
            "BANK Cross-VLM verify is on but BANK_CROSS_VLM_MODEL is empty. "
            "Configure Settings → API → Bank Cross-VLM model."
        )
    # Explicit cross credentials, else VLM credentials — but never hardcoded vendor URL
    api_key = (
        (os.getenv("BANK_CROSS_VLM_API_KEY") or "").strip()
        or (os.getenv("VLM_API_KEY") or "").strip()
    )
    base_url = (
        (os.getenv("BANK_CROSS_VLM_BASE_URL") or "").strip()
        or (os.getenv("VLM_BASE_URL") or "").strip()
    )
    if not api_key or not base_url:
        raise ValueError(
            "BANK Cross-VLM credentials incomplete. Set Bank Cross-VLM or VLM "
            "API key and URL in Settings → API."
        )
    return {"model": model, "api_key": api_key, "api_url": base_url}


def _env_float(key: str, default: float) -> float:
    raw = (os.getenv(key, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = (os.getenv(key, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "Bookcomet Backend")
    app_env: str = os.getenv("APP_ENV", "local")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    ocr_provider: str = (
        os.getenv("OCR_PROVIDER", _DEFAULT_OCR_ALIAS).strip() or _DEFAULT_OCR_ALIAS
    )
    # Text LLM gateway model (manager review, chat, gate, account coding).
    # Neutral name LLM_MODEL preferred; legacy DEPLOY_MODEL still accepted.
    deploy_model: str = (
        (os.getenv("LLM_MODEL") or os.getenv("DEPLOY_MODEL") or "").strip()
        or _DEFAULT_DEPLOY_MODEL
    )
    # Document gate LLM: GATE_MODEL if set, else deploy_model
    gate_model_env: str = os.getenv("GATE_MODEL", "").strip()
    # BANK mode VLM id — Settings → API → VLM unless BANK_VLM_MODEL override set.
    bank_vlm_model: str = (
        (os.getenv("BANK_VLM_MODEL") or os.getenv("VLM_MODEL") or "").strip()
        or _DEFAULT_VLM_MODEL
    )
    # Default OCR payload model (OpenAI-compatible VLM gateway).
    vlm_model: str = (
        (os.getenv("VLM_MODEL") or "").strip() or _DEFAULT_VLM_MODEL
    )
    # AP VLM when AP_VLM_MODEL and AP_MULTI_RECEIPT_OCR_MODEL are both unset
    ap_vlm_default: str = (
        os.getenv("AP_VLM_DEFAULT", _DEFAULT_OCR_ALIAS).strip() or _DEFAULT_OCR_ALIAS
    )
    # invoice vs receipts layout classifier (single-word VLM)
    document_layout_classify_model: str = (
        os.getenv("DOCUMENT_LAYOUT_CLASSIFY_MODEL", _DEFAULT_LAYOUT_CLASSIFY_MODEL).strip()
        or _DEFAULT_LAYOUT_CLASSIFY_MODEL
    )
    ai_enhance_reasoner_model: str = (
        (os.getenv("AI_ENHANCE_REASONER_MODEL") or "").strip()
        or _DEFAULT_AI_ENHANCE_REASONER_MODEL
    )

    # Database
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./ai_accounting.db")
    # Connection pool (PostgreSQL/MySQL; ignored for typical SQLite URLs)
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "10"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "60"))
    db_pool_recycle: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    # Max concurrent OCR + AI chat cores holding a DB session (app-wide cap)
    db_heavy_work_concurrency: int = int(os.getenv("DB_HEAVY_WORK_CONCURRENCY", "8"))
    # Non-SQLite only: when APP_ENV=local, run create_all on startup. SQLite file DBs
    # always run create_all + column patches in main.startup_event regardless of this flag.
    db_auto_create_on_startup: bool = os.getenv(
        "DB_AUTO_CREATE_ON_STARTUP", "false"
    ).lower() in ("true", "1", "yes")

    # Redis — rate limits + OCR concurrency across instances (optional; empty = in-process only)
    redis_url: str = os.getenv("REDIS_URL", "").strip()
    redis_key_prefix: str = os.getenv("REDIS_KEY_PREFIX", "bookcomet").strip() or "bookcomet"
    trust_forwarded_headers: bool = os.getenv("TRUST_FORWARDED_HEADERS", "").lower() in (
        "true",
        "1",
        "yes",
    )
    auth_login_max_per_minute_per_ip: int = int(os.getenv("AUTH_LOGIN_MAX_PER_MINUTE_PER_IP", "30"))
    auth_register_max_per_minute_per_ip: int = int(os.getenv("AUTH_REGISTER_MAX_PER_MINUTE_PER_IP", "10"))

    # AI enhancement chat LLM (post-OCR field extraction).
    ai_enhance_api_key: str = (
        os.getenv("AI_ENHANCE_API_KEY")
        or os.getenv("LLM_API_KEY")
        or os.getenv("VLM_API_KEY")
        or ""
    )
    ai_enhance_api_base: str = (
        os.getenv("AI_ENHANCE_BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or os.getenv("VLM_BASE_URL")
        or "https://www.dmxapi.cn"
    )
    ai_enhance_model: str = (
        (os.getenv("AI_ENHANCE_MODEL") or os.getenv("LLM_MODEL") or "").strip()
        or deploy_model
    )
    ai_enhance_use_reasoner: bool = (
        os.getenv("AI_ENHANCE_USE_REASONER") or "false"
    ).lower() in ("true", "1", "yes")
    # Primary VLM/OCR gateway (OpenAI-compatible). No vendor URL default —
    # leave blank until configured (Settings → API → VLM).
    vlm_api_key: str = os.getenv("VLM_API_KEY") or os.getenv("LLM_API_KEY") or ""
    vlm_api_base: str = (
        os.getenv("VLM_BASE_URL") or os.getenv("LLM_BASE_URL") or ""
    )

    # JWT / Auth (SEC-CODE-001: no weak default — set JWT_SECRET_KEY in .env)
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24h
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "365"))
    # Empty = open self-register on pure localhost. Required when tunnel-like (SEC-CODE-002).
    register_invite_code: str = os.getenv("REGISTER_INVITE_CODE", "").strip()

    # Email (leave blank in dev to use console logging)
    mail_username: str = os.getenv("MAIL_USERNAME", "")
    mail_password: str = os.getenv("MAIL_PASSWORD", "")
    mail_from: str = os.getenv("MAIL_FROM", "noreply@example.com")
    mail_server: str = os.getenv("MAIL_SERVER", "")
    mail_port: int = int(os.getenv("MAIL_PORT", "587"))
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173")

    # File storage (local disk; swap FILE_STORAGE_BACKEND=s3 in production)
    uploads_dir: str = os.getenv("UPLOADS_DIR", "./uploads")
    file_storage_backend: str = os.getenv("FILE_STORAGE_BACKEND", "local")
    # Optional Fernet wrap for local upload files (SEC-PUB-003). Empty = plaintext on disk.
    uploads_encryption_key: str = os.getenv("UPLOADS_ENCRYPTION_KEY", "")
    max_upload_size_mb: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    # Keep OCR background job upload on disk for retry-page API (hours). 0 = delete when the job finishes (legacy).
    ocr_job_upload_retention_hours: int = int(os.getenv("OCR_JOB_UPLOAD_RETENTION_HOURS", "24"))

    # Process-oriented intake (unified upload → classify → confirm → OCR). Default off for safe rollback.
    feature_process_intake_v2: bool = os.getenv(
        "FEATURE_PROCESS_INTAKE_V2", ""
    ).lower() in ("true", "1", "yes")
    # AP second VLM (AP_CROSS_VLM_MODEL): optional structured re-extraction + in-place merge
    ap_cross_vlm_model: str = os.getenv("AP_CROSS_VLM_MODEL", "").strip()
    ap_auto_cross_verify_enabled: bool = os.getenv(
        "AP_AUTO_CROSS_VERIFY_ENABLED", "true"
    ).lower() in ("true", "1", "yes", "on")
    ap_auto_cross_verify_policy: str = (
        os.getenv("AP_AUTO_CROSS_VERIFY_POLICY", "aggressive_overwrite").strip()
        or "aggressive_overwrite"
    )
    ap_auto_cross_verify_confidence_threshold: float = _env_float(
        "AP_AUTO_CROSS_VERIFY_CONFIDENCE_THRESHOLD", 0.0
    )
    # Skip the cross pass entirely when primary min row confidence is at or above
    # this value (0-1 scale). 0 disables the skip. Manual Double check always runs.
    ap_auto_cross_verify_skip_primary_confidence: float = _env_float(
        "AP_AUTO_CROSS_VERIFY_SKIP_PRIMARY_CONFIDENCE", 0.0
    )
    ap_auto_cross_verify_timeout_ms: int = max(
        1000, _env_int("AP_AUTO_CROSS_VERIFY_TIMEOUT_MS", 120000)
    )
    # Independent local-OCR cross-check (flag-only) for AP rows. Empty/off disables.
    # "paddle" uses local PaddleOCR (requires requirements-ocr.txt; Windows-risky).
    # Leaves room for "cloud" in future.
    ap_ocr_cross_check_provider: str = os.getenv("AP_OCR_CROSS_CHECK_PROVIDER", "").strip()
    # Minimum fraction of merchant-name units that must appear in the independent
    # OCR text before the merchant is considered a match (0-1).
    ap_ocr_cross_check_merchant_min_overlap: float = _env_float(
        "AP_OCR_CROSS_CHECK_MERCHANT_MIN_OVERLAP", 0.5
    )


_DEFAULT_JWT_DEV_SECRET = "dev-secret-change-in-production"
_MIN_JWT_SECRET_LEN = 32


def _allow_insecure_dev_jwt() -> bool:
    return os.getenv("ALLOW_INSECURE_DEV_JWT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _host_is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1")


def _cors_origins_from_env() -> list[str]:
    default = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174"
    )
    raw = os.getenv("CORS_ORIGINS", default)
    return [o.strip() for o in raw.split(",") if o.strip()]


def _origin_is_localhost(origin: str) -> bool:
    o = (origin or "").strip().lower()
    return (
        o.startswith("http://localhost:")
        or o.startswith("http://127.0.0.1:")
        or o.startswith("https://localhost:")
        or o.startswith("https://127.0.0.1:")
        or o in ("http://localhost", "http://127.0.0.1")
    )


def is_tunnel_like_exposure(s: "Settings | None" = None) -> bool:
    """True when the API is (or may be) reachable beyond pure local loopback UI."""
    cfg = s or settings
    if not _host_is_loopback(cfg.host):
        return True
    if cfg.trust_forwarded_headers:
        return True
    origins = _cors_origins_from_env()
    if any(o == "*" or not _origin_is_localhost(o) for o in origins):
        return True
    return False


def _validate_security_settings(s: Settings) -> None:
    """SEC-CODE-001 / SEC-CODE-002 boot-time security gates."""
    key = (s.jwt_secret_key or "").strip()
    weak = (not key) or key == _DEFAULT_JWT_DEV_SECRET or len(key) < _MIN_JWT_SECRET_LEN
    exposed = is_tunnel_like_exposure(s)

    if weak:
        # Never allow weak JWT when the process may be reachable beyond loopback.
        if _allow_insecure_dev_jwt() and s.app_env == "local" and not exposed:
            pass
        else:
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a random string of at least "
                f"{_MIN_JWT_SECRET_LEN} characters. "
                "Generate one with: openssl rand -base64 64. "
                "ALLOW_INSECURE_DEV_JWT=true is only permitted for APP_ENV=local "
                "on loopback without tunnel-like CORS/proxy settings "
                f"(current APP_ENV={s.app_env!r}, HOST={s.host!r})."
            )

    # SEC-CODE-002: invite code required whenever exposure looks tunnel/public.
    if exposed and not (s.register_invite_code or "").strip():
        raise RuntimeError(
            "REGISTER_INVITE_CODE is required when the API may be reachable beyond "
            "localhost (non-loopback HOST, TRUST_FORWARDED_HEADERS=true, or "
            "non-localhost CORS_ORIGINS). Set a long random invite code before "
            "enabling a tunnel."
        )

    # SEC-CODE-003: reject wildcard CORS origins.
    if any(o.strip() == "*" for o in _cors_origins_from_env()):
        raise RuntimeError(
            "CORS_ORIGINS must not include '*'. List explicit origins instead."
        )


settings = Settings()
_validate_security_settings(settings)


def workflow_provider_options() -> list[str]:
    """Provider labels for workflow node dropdowns (WORKFLOW_PROVIDERS env, comma-separated)."""
    raw = (os.getenv("WORKFLOW_PROVIDERS") or "Qwen").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def default_workflow_provider() -> str:
    opts = workflow_provider_options()
    return opts[0] if opts else "Qwen"


def _workflow_provider_gateway_map() -> dict[str, str]:
    raw = (os.getenv("WORKFLOW_PROVIDER_GATEWAYS") or "").strip()
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in raw.split(","):
        piece = part.strip()
        if "=" not in piece:
            continue
        label, gateway = piece.split("=", 1)
        label = label.strip()
        gateway = gateway.strip().lower()
        if label and gateway:
            out[label] = gateway
    return out


def workflow_model_options() -> list[str]:
    """Model ids selectable in workflow node dropdowns, derived from .env settings."""
    candidates = [
        settings.deploy_model,
        settings.ai_enhance_model,
        settings.ai_enhance_reasoner_model,
        settings.vlm_model,
        settings.bank_vlm_model,
        settings.ap_cross_vlm_model,
    ]
    seen: set[str] = set()
    out: list[str] = []
    for raw in candidates:
        value = (raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def resolve_gateway(model: str | None, provider: str | None = None) -> str:
    """Map model + provider label to HTTP client gateway: ai_enhance or llm."""
    value = (model or "").strip()
    enhance_models = {
        m for m in (settings.ai_enhance_model, settings.ai_enhance_reasoner_model) if m
    }
    if value and value in enhance_models:
        return "ai_enhance"
    provider_label = (provider or "").strip()
    if provider_label:
        gateway = _workflow_provider_gateway_map().get(provider_label, "").lower()
        if gateway == "ai_enhance":
            return "ai_enhance"
    return "llm"


def normalize_workflow_provider_label(provider: str | None) -> str:
    """Map legacy graph provider labels to a configured workflow provider."""
    label = (provider or "").strip()
    if not label or label.lower() == "deepseek":
        return default_workflow_provider()
    opts = workflow_provider_options()
    if label in opts:
        return label
    return default_workflow_provider()


def resolved_gate_llm_model(s: Settings) -> str:
    """LLM id for document gate: GATE_MODEL when set, else DEPLOY_MODEL."""
    return s.gate_model_env or s.deploy_model

