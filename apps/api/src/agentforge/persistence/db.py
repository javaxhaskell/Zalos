"""SQLAlchemy engine + session factory.

SQLite with WAL mode for safe concurrent read + write at single-user scale.
Postgres migration is documented in ``DEPLOYMENT.md`` as a production extension.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base shared by all ORM models."""


def _enable_sqlite_wal(dbapi_connection: DBAPIConnection, connection_record: Any) -> None:
    """Enable WAL + foreign keys for SQLite connections.

    Registered as a ``connect`` event listener. No-op for non-SQLite drivers.
    """
    if hasattr(dbapi_connection, "execute"):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        except Exception:
            # Non-SQLite driver; skip silently.
            pass


def init_engine(database_url: str) -> Engine:
    """Initialise (or return existing) global engine + session factory."""
    global _engine, _session_factory
    if _engine is None:
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(
            database_url,
            future=True,
            connect_args=connect_args,
            echo=False,
        )
        if database_url.startswith("sqlite"):
            event.listen(_engine, "connect", _enable_sqlite_wal)
        _session_factory = sessionmaker(
            bind=_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _engine


def get_engine() -> Engine:
    """Return the initialised engine (raises if not yet init'd)."""
    if _engine is None:
        raise RuntimeError("init_engine() has not been called.")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("init_engine() has not been called.")
    return _session_factory


def dispose_engine() -> None:
    """Tear down the engine (for shutdown / tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
