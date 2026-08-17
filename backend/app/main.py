import asyncio
import logging
import os

# ============================================================================
# NOTE: PaddleOCR has been removed due to oneDNN/PIR compatibility issues on Windows
# The system now uses EasyOCR as the primary OCR provider
# ============================================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.redis_client import close_async_redis, close_sync_redis
from app.middleware.auth_ip_rate_limit import AuthIpRateLimitMiddleware
from app.core.config import _cors_origins_from_env, settings
from app.core.logging import configure_logging
from sqlalchemy import text

from app.database import Base, engine
import app.models  # Ensure models are registered before create_all


configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CORS — configure via CORS_ORIGINS env var (comma-separated).
# SEC-CODE-003: explicit methods/headers; no wildcard origins (validated at boot).
# ---------------------------------------------------------------------------
CORS_ORIGINS: list[str] = _cors_origins_from_env()
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = [
    "Accept",
    "Authorization",
    "Content-Type",
    "X-Company-ID",
    "X-Trace-ID",
    "X-Request-ID",
    "X-User-ID",
]

_OPENAPI_TAGS: list[dict] = [
    {"name": "auth",            "description": "User registration, login, token refresh, and password management."},
    {"name": "health",          "description": "Service health check."},
    {"name": "ocr",             "description": "Document OCR and AI-enhanced field extraction (AR / AP / BANK / OTHER)."},
    {"name": "bank-statements", "description": "Bank statement upload (sync and async), job status polling, and page counting."},
    {"name": "reconciliation",  "description": "Chart of Accounts CRUD, account code deploy, and OCR transaction category updates."},
    {"name": "ocr-journals",    "description": "Draft double-entry journals linked to OCR bank/ledger transaction rows (export, no posting in v1)."},
    {"name": "settings",        "description": "AI provider configuration."},
    {"name": "identity",        "description": "User, company, and membership bootstrap management."},
    {"name": "company-context", "description": "Company profile, industry settings, and accounting manual."},
    {"name": "compliance",      "description": "Audit trail lookup and audit package generation, archiving, and verification."},
    {"name": "ai-chat",         "description": "AI assistant chat with structured table-patch responses and rule learning."},
    {"name": "tasks",           "description": "Chat task CRUD, message history, file attachments, state snapshots, and audit log."},
    {"name": "token-usage",     "description": "LLM token consumption and cost aggregation per period and call type."},
    {"name": "other", "description": "Other (asset/liability) record extraction, CRUD, and depreciation schedules."},
    {"name": "rule-memory",     "description": "Per-company Markdown rule memory: keyword rules, vendor rules, defaults, and AI instructions. Version history and export/import."},
    {"name": "company-manual",  "description": "Company accounting manual — versioned Markdown document for AI context injection."},
    {"name": "exclusion-rules", "description": "Per-company exclusion rules that flag matching transactions for manual review instead of AI processing."},
    {"name": "dashboard",       "description": "ROI metrics dashboard: task counts, pages processed, AI completion rate, and cost by mode."},
]

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Bookcomet — AI-powered accounting platform for Hong Kong SMEs. "
        "Provides OCR document extraction, rule-based classification, "
        "OCR journals (draft), and AI chat assistance.\n\n"
        "**Authentication:** Most endpoints require a `Authorization: Bearer <token>` header "
        "obtained from `POST /auth/login`, plus an `X-Company-ID` header.\n\n"
        "**Rate limits:** AI endpoints are limited per company (see AbuseGuard env vars). "
        "With `REDIS_URL` set, limits and OCR concurrency are shared across all API instances. "
        "`POST /auth/login` and `POST /auth/register` are limited per IP."
    ),
    contact={"name": "AI Accounting Support", "email": "support@example.com"},
    openapi_tags=_OPENAPI_TAGS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)
app.add_middleware(AuthIpRateLimitMiddleware)


@app.on_event("startup")
async def startup_event():
    """Pre-initialize OCR service on startup"""
    # SQLite (default DATABASE_URL): always ensure ORM tables exist and patch older
    # files with new columns so local runs don't 500 on ledger import or document gate.
    if engine.dialect.name == "sqlite":
        logger.info("SQLite: ensuring tables and column patches (startup bootstrap).")
        Base.metadata.create_all(bind=engine)
        _ensure_sqlite_schema()
    elif settings.db_auto_create_on_startup and settings.app_env == "local":
        logger.warning(
            "DB_AUTO_CREATE_ON_STARTUP is enabled for non-SQLite local. "
            "Prefer Alembic migrations for PostgreSQL/MySQL."
        )
        Base.metadata.create_all(bind=engine)
        _ensure_sqlite_schema()
    elif settings.db_auto_create_on_startup:
        logger.warning(
            "Ignoring DB_AUTO_CREATE_ON_STARTUP because APP_ENV=%r is not local.",
            settings.app_env,
        )
    logger.info("Initializing OCR service...")
    try:
        from app.database import SessionLocal
        from app.graph.graph_migrate import migrate_all_saved_graphs

        with SessionLocal() as db:
            migrated = migrate_all_saved_graphs(db)
            if migrated:
                logger.info("Migrated %s workflow graph(s) to schema V2.", migrated)
    except Exception as exc:
        logger.warning("Workflow graph V2 migration skipped: %s", exc)
    try:
        from app.ocr.runtime import get_ocr_service
        _ocr_service = get_ocr_service()
        logger.info(f"OCR provider: {_ocr_service._provider_name}")
        logger.info("OCR service ready")
    except Exception as e:
        logger.warning(f"Failed to pre-initialize OCR service: {e}")
        logger.warning("OCR service will initialize on first request")


@app.on_event("shutdown")
async def shutdown_event():
    """Dispose DB pool and Redis; blocking sync IO runs in a worker thread so the loop can process shutdown signals."""
    logger.info("Closing database connections...")
    try:
        await asyncio.wait_for(asyncio.to_thread(engine.dispose), timeout=15.0)
    except asyncio.TimeoutError:
        logger.warning("engine.dispose() timed out; continuing shutdown.")
    except Exception as e:
        logger.warning("engine.dispose() failed: %s", e)
    logger.info("Database connections closed.")
    try:
        await asyncio.wait_for(close_async_redis(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Redis async client close timed out; continuing shutdown.")
    except Exception as e:
        logger.warning("Redis async client close failed: %s", e)
    try:
        await asyncio.wait_for(asyncio.to_thread(close_sync_redis), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Redis sync client close timed out; continuing shutdown.")
    except Exception as e:
        logger.warning("Redis sync client close failed: %s", e)


def _sqlite_table_exists(connection, table_name: str) -> bool:
    row = connection.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :n LIMIT 1"
        ),
        {"n": table_name},
    ).fetchone()
    return row is not None


def _ensure_sqlite_schema() -> None:
    """Add missing columns in SQLite when models evolve."""
    if engine.dialect.name != "sqlite":
        return

    column_map = {
        "users": {
            "username": "TEXT",
            "hashed_password": "TEXT",
            "is_active": "INTEGER DEFAULT 1",
            "is_verified": "INTEGER DEFAULT 1",
            "verification_token": "TEXT",
            "verification_token_expiry": "DATETIME",
            "reset_token": "TEXT",
            "reset_token_expiry": "DATETIME",
            "refresh_token_hash": "TEXT",
            "refresh_token_expires_at": "DATETIME",
            "last_login_at": "DATETIME",
            "failed_login_count": "INTEGER DEFAULT 0",
            "locked_until": "DATETIME",
            "session_version": "INTEGER DEFAULT 0",
            "mfa_enabled": "INTEGER DEFAULT 0",
            "mfa_secret": "TEXT",
        },
        "bank_transactions": {
            "company_id": "TEXT DEFAULT 'default'",
            "import_batch_id": "TEXT",
            "status": "TEXT DEFAULT 'unreconciled'",
        },
        "ledger_transactions": {
            "company_id": "TEXT DEFAULT 'default'",
            "import_batch_id": "TEXT",
            "status": "TEXT DEFAULT 'unreconciled'",
            "dr_cr": "TEXT",
        },
        "reconciliation_match": {
            "company_id": "TEXT DEFAULT 'default'",
            "trace_id": "TEXT",
        },
        "reconciliation_audit": {
            "company_id": "TEXT DEFAULT 'default'",
            "trace_id": "TEXT",
        },
        "company_rules": {
            "hit_count": "INTEGER DEFAULT 0",
            "last_hit_at": "DATETIME",
            "last_hit_source": "TEXT",
        },
        "company_rule_hit_events": {
            "trace_id": "TEXT",
        },
        "company_rule_audit_logs": {
            "trace_id": "TEXT",
        },
        "company_profiles": {
            "company_name": "TEXT",
            "company_name_keywords": "JSON",
            "profile_md": "TEXT",
        },
        "company_rule_memories": {
            "updated_by_user": "TEXT",
            "updated_by_type": "TEXT DEFAULT 'user'",
        },
        "company_rule_memory_versions": {
            "saved_by": "TEXT",
            "saved_by_type": "TEXT DEFAULT 'user'",
        },
        "company_manuals": {
            "updated_by_user": "TEXT",
            "updated_by_type": "TEXT DEFAULT 'user'",
        },
        "company_manual_versions": {
            "saved_by": "TEXT",
            "saved_by_type": "TEXT DEFAULT 'user'",
        },
        "exclusion_rules": {
            "hit_count": "INTEGER DEFAULT 0",
            "last_hit_at": "DATETIME",
            "modes": "TEXT",
            "reason": "TEXT",
        },
        "workflow_runs": {
            "folder_id": "TEXT",
            "archived_at": "DATETIME",
        },
    }

    with engine.begin() as connection:
        for table_name, columns in column_map.items():
            if not _sqlite_table_exists(connection, table_name):
                logger.debug(
                    "SQLite schema: skip %s (table not present — e.g. not created yet)",
                    table_name,
                )
                continue
            result = connection.execute(text(f"PRAGMA table_info({table_name})"))
            existing = {row[1] for row in result.fetchall()}
            for column_name, column_type in columns.items():
                if column_name in existing:
                    continue
                try:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )
                    logger.info(
                        "SQLite schema updated: added %s.%s",
                        table_name,
                        column_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to add column %s.%s: %s",
                        table_name,
                        column_name,
                        exc,
                    )

        # Backfill username for legacy rows (local SQLite without alembic).
        if _sqlite_table_exists(connection, "users"):
            cols = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(users)")).fetchall()
            }
            if "username" in cols:
                rows = connection.execute(
                    text("SELECT id, email, username FROM users WHERE username IS NULL OR TRIM(username) = ''")
                ).fetchall()
                used = {
                    str(r[0]).lower()
                    for r in connection.execute(
                        text("SELECT username FROM users WHERE username IS NOT NULL AND TRIM(username) != ''")
                    ).fetchall()
                }
                for uid, email, _existing in rows:
                    local = (str(email or "").split("@", 1)[0] or "").lower()
                    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in local).strip("._-")[:64]
                    if len(cleaned) < 3:
                        cleaned = f"user_{str(uid).replace('-', '')[:12]}"
                    candidate = cleaned
                    n = 2
                    while candidate in used:
                        suffix = f"_{n}"
                        candidate = f"{cleaned[: max(1, 64 - len(suffix))]}{suffix}"
                        n += 1
                    used.add(candidate)
                    connection.execute(
                        text("UPDATE users SET username = :u WHERE id = :id"),
                        {"u": candidate, "id": uid},
                    )
                connection.execute(
                    text("UPDATE users SET is_verified = 1 WHERE is_verified IS NULL OR is_verified = 0")
                )


app.include_router(api_router)

