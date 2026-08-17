import logging
import os
import re
from pathlib import Path

from app.core.config import settings


class SensitiveDataFilter(logging.Filter):
    """SEC-CODE-005: mask secrets, tokens, and common credential patterns in logs."""

    PATTERNS: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"(api[_-]?key[=:\s]+)[^\s,;\"']+", re.I), r"\1***"),
        (re.compile(r"(password[=:\s]+)[^\s,;\"']+", re.I), r"\1***"),
        (re.compile(r"(secret[=:\s]+)[^\s,;\"']+", re.I), r"\1***"),
        (re.compile(r"(token[=:\s]+)[^\s,;\"']+", re.I), r"\1***"),
        (re.compile(r"(bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
        (re.compile(r"(authorization[=:\s]+)[^\s,;\"']+", re.I), r"\1***"),
        (re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"), "sk-***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern, replacement in self.PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging() -> None:
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "backend.log"

    # SEC-CODE-005: quieter default outside local unless LOG_LEVEL is explicitly set.
    explicit_level = (os.getenv("LOG_LEVEL") or "").strip()
    if explicit_level:
        level_name = explicit_level
    elif (settings.app_env or "").strip().lower() == "local":
        level_name = "INFO"
    else:
        level_name = "WARNING"

    level = getattr(logging, str(level_name).upper(), logging.INFO)

    root = logging.getLogger()
    # Avoid duplicate handlers when configure_logging is called more than once.
    if not root.handlers:
        handlers: list[logging.Handler] = [
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ]
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            handlers=handlers,
        )
    else:
        root.setLevel(level)

    sensitive = SensitiveDataFilter()
    for handler in logging.root.handlers:
        # Avoid stacking identical filters on reload.
        if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
            handler.addFilter(sensitive)
    app_logger = logging.getLogger("app")
    if not any(isinstance(f, SensitiveDataFilter) for f in app_logger.filters):
        app_logger.addFilter(sensitive)
