import sys
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlmodel import SQLModel  # noqa: E402

from src.observability import configure_logging  # noqa: E402
from src.settings import settings  # noqa: E402
from src.storage import models  # noqa: E402,F401 - registers tables on SQLModel.metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Deliberately NOT calling fileConfig(config.config_file_name) here, unlike
# alembic's default template - confirmed live that it unconditionally
# replaces the root logger's handler/level, which would silently swap out
# the app's JSON logging (src/observability.py) for a plain-text one at
# WARNING level on every `alembic upgrade head` call (i.e. every app/script
# startup via init_db()). configure_logging() is idempotent, so calling it
# here too is enough to guarantee alembic's own log messages go through
# the same structured handler even when env.py runs standalone (`alembic`
# CLI) rather than via init_db().
configure_logging()

# Overrides alembic.ini's placeholder URL with the same builder db.py's
# engine uses, so migrations always target whatever MYSQL_* env vars the
# app itself would connect to - one source of truth, not two.
config.set_main_option("sqlalchemy.url", settings.mysql_url)

target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
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
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
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
