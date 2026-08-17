from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context

# Import Base and models
from app.core.config import settings
from app.core.db_url import build_sqlalchemy_url, create_db_engine
from app.database import Base
from app.models import *  # noqa

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Keep Alembic pointed at the same database URL as the application instead of
# relying on the static sqlite URL in alembic.ini.
# Percent-encode for ConfigParser (% → %%); SQLCipher password may contain %.
_resolved_url, _ = build_sqlalchemy_url(settings.database_url)
config.set_main_option("sqlalchemy.url", _resolved_url.replace("%", "%%"))

# add your model's MetaData object here
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (honors DATABASE_PASSWORD / SQLCipher)."""
    connectable = create_db_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
