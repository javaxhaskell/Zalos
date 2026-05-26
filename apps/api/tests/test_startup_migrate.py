"""Startup migration guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from agentforge.persistence import db as db_module
from agentforge.persistence.migrate import ensure_migrations_applied, schema_missing_required_columns


@pytest.fixture(autouse=True)
def _reset_engine() -> None:
    db_module._engine = None
    db_module._session_factory = None
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._session_factory = None


def test_schema_missing_required_columns_detects_deleted_at(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, "
                "workflow TEXT NOT NULL, "
                "status TEXT NOT NULL"
                ")"
            )
        )

    missing = schema_missing_required_columns(engine)
    assert "deleted_at" in missing
    assert "status_before_archive" in missing
    engine.dispose()


def test_ensure_migrations_applied_runs_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{db_path}"
    calls: list[tuple[object, str]] = []

    def _fake_upgrade(cfg: object, revision: str) -> None:
        calls.append((cfg, revision))
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE sessions ("
                    "id TEXT PRIMARY KEY, "
                    "workflow TEXT NOT NULL, "
                    "status TEXT NOT NULL, "
                    "deleted_at DATETIME, "
                    "status_before_archive TEXT"
                    ")"
                )
            )
        engine.dispose()

    monkeypatch.setattr(
        "agentforge.persistence.migrate.command.upgrade",
        _fake_upgrade,
    )

    ensure_migrations_applied(database_url)

    assert len(calls) == 1
    assert calls[0][1] == "head"

    verify_engine = create_engine(database_url)
    assert schema_missing_required_columns(verify_engine) == set()
    verify_engine.dispose()
