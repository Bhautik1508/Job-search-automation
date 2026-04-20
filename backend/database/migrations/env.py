"""
Alembic migration environment.

URL and target metadata are pulled from the project's own config so that
`alembic upgrade head` and application code always agree on the database.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from backend.database.models import Base

config = context.config

# Pull the URL from the project's config only if the caller hasn't already
# supplied one (tests pass an explicit per-fixture URL via Config.set_main_option).
if not config.get_main_option("sqlalchemy.url"):
    # Import late so DATABASE_URL is recomputed under any active env-var overrides.
    from backend.config import DATABASE_URL
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
