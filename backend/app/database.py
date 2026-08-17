from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.db_url import create_db_engine, is_sqlite_url

# Public alias kept for existing imports / scripts.
DATABASE_URL = settings.database_url

_is_sqlite = is_sqlite_url(DATABASE_URL)

# SQLite needs check_same_thread=False so the connection pool can be safely
# disposed from a different thread during shutdown.
_connect_args = {"check_same_thread": False, "timeout": 5} if _is_sqlite else {}

_engine_kwargs: dict = {
    "echo": settings.app_env == "development",
    "connect_args": _connect_args,
}
# QueuePool tuning for PostgreSQL/MySQL; SQLite file URLs use NullPool by default.
if not _is_sqlite:
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
    )

# SEC-CODE-007: when DATABASE_PASSWORD is set, this becomes SQLCipher-backed.
engine = create_db_engine(DATABASE_URL, **_engine_kwargs)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


# Dependency for FastAPI routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
