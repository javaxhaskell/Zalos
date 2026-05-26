"""Apply pending Alembic migrations on API startup (local dev safety net).

``make api`` already runs ``make migrate`` first; this guard covers direct
``uvicorn`` invocations and stale SQLite files after pulling new revisions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

_logger = logging.getLogger("agentforge.migrate")

_API_DIR = Path(__file__).resolve().parents[3]
_REQUIRED_SESSION_COLUMNS = frozenset({"deleted_at", "status_before_archive"})


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def schema_missing_required_columns(engine: Engine) -> set[str]:
    """Return required session columns absent from the live DB schema."""
    inspector = inspect(engine)
    if "sessions" not in inspector.get_table_names():
        return set(_REQUIRED_SESSION_COLUMNS)
    present = {column["name"] for column in inspector.get_columns("sessions")}
    return _REQUIRED_SESSION_COLUMNS - present


def ensure_migrations_applied(database_url: str) -> None:
    """Upgrade to Alembic head when archive/delete columns are absent."""
    from agentforge.persistence.db import get_engine, init_engine

    init_engine(database_url)
    missing = schema_missing_required_columns(get_engine())
    if not missing:
        return

    _logger.warning(
        "database schema is behind (missing columns: %s); running alembic upgrade head",
        ", ".join(sorted(missing)),
    )
    command.upgrade(_alembic_config(database_url), "head")

    still_missing = schema_missing_required_columns(get_engine())
    if still_missing:
        raise RuntimeError(
            "Database schema is missing required columns after migration: "
            f"{', '.join(sorted(still_missing))}. "
            "Run `make migrate` from the repo root against the DATABASE_URL in .env."
        )
