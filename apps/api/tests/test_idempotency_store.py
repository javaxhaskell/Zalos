"""Tests for the :class:`IdempotencyStore` (BP5a, INV-7).

The store is a thin SQLite-backed repository over the existing
``idempotency_keys`` table. The contract the agent loop relies on:

  * ``get`` returns ``None`` for unseen keys.
  * ``put`` stores a new (key, request_hash, payload) triple.
  * ``put`` of an existing key with the same ``request_hash`` is a
    no-op that returns the cached entry.
  * ``put`` of an existing key with a *different* ``request_hash``
    raises :class:`IdempotencyConflictError` — the executor surfaces
    this back to the model as a typed observation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.persistence.db import Base
from agentforge.persistence.idempotency_store import (
    IdempotencyConflictError,
    IdempotencyStore,
    request_hash_for,
)


@pytest.fixture()
def db_session(tmp_path: Path):
    """Per-test SQLite + sessionmaker with all tables created."""
    url = f"sqlite:///{tmp_path / 'idem.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_get_returns_none_for_missing_key(db_session) -> None:
    store = IdempotencyStore(db=db_session)
    assert store.get("never-seen") is None


def test_put_then_get_round_trip(db_session) -> None:
    store = IdempotencyStore(db=db_session)
    canonical = '{"path":"uploads/foo.csv"}'
    request_hash = request_hash_for(canonical)
    payload = {"file_id": "abc", "row_count": 200}

    cached = store.put(
        key="k1",
        tool_name="inspect_csv_schema",
        request_hash=request_hash,
        response_payload=payload,
    )
    assert cached.key == "k1"
    assert cached.payload == payload

    fetched = store.get("k1")
    assert fetched is not None
    assert fetched.tool_name == "inspect_csv_schema"
    assert fetched.payload == payload
    assert fetched.request_hash == request_hash


def test_put_idempotent_for_same_request_hash(db_session) -> None:
    store = IdempotencyStore(db=db_session)
    canonical = '{"path":"uploads/foo.csv"}'
    request_hash = request_hash_for(canonical)
    payload = {"file_id": "abc"}

    first = store.put(
        key="k2",
        tool_name="t",
        request_hash=request_hash,
        response_payload=payload,
    )
    second = store.put(
        key="k2",
        tool_name="t",
        request_hash=request_hash,
        response_payload={"file_id": "different-but-ignored"},
    )
    # Second put returns the cached entry untouched.
    assert second.payload == first.payload == payload


def test_put_with_different_request_hash_raises(db_session) -> None:
    store = IdempotencyStore(db=db_session)
    store.put(
        key="k3",
        tool_name="t",
        request_hash=request_hash_for('{"path":"a.csv"}'),
        response_payload={"v": 1},
    )

    with pytest.raises(IdempotencyConflictError, match="different request_hash"):
        store.put(
            key="k3",
            tool_name="t",
            request_hash=request_hash_for('{"path":"b.csv"}'),
            response_payload={"v": 2},
        )


def test_request_hash_is_deterministic_and_unique() -> None:
    h1 = request_hash_for('{"path":"a"}')
    h2 = request_hash_for('{"path":"a"}')
    h3 = request_hash_for('{"path":"b"}')
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64
