"""Alembic environment configuration.

Uses SQLite batch mode (render_as_batch=True) to work around SQLite's
limited ALTER TABLE support.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
    )

    with connectable.connect() as connection:
        # Disable FK enforcement during batch operations
        connection.execute(text("PRAGMA foreign_keys=OFF"))

        context.configure(
            connection=connection,
            target_metadata=None,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()

        # Re-enable FK enforcement and verify integrity
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("PRAGMA foreign_key_check"))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
