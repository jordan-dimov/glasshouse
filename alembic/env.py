"""Alembic environment: app schema only.

`GLASSHOUSE_DATABASE_URL` overrides the ini URL (twelve-factor, same
variable the application reads); libpq-style URLs are normalised to the
psycopg3 dialect.
"""

from __future__ import annotations

import os

from sqlalchemy import engine_from_config, pool

from alembic import context
from glasshouse.compute.store import engine_url, metadata

config = context.config
override = os.environ.get("GLASSHOUSE_DATABASE_URL")
if override:
    config.set_main_option("sqlalchemy.url", engine_url(override))

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
