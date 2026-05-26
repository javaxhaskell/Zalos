"""Idempotency cache for tool dispatches (INV-7).

Backed by the ``idempotency_keys`` table (declared in
:mod:`agentforge.persistence.models` and created in the baseline
migration). The store has only two operations:

  * :meth:`get` — return the cached observation payload for a key,
    or ``None`` if the key has never been seen.
  * :meth:`put` — store a new (key, request_hash, payload) triple, or
    raise :class:`IdempotencyConflictError` if the key already exists
    with a different ``request_hash``.

The agent loop derives the key via
:func:`agentforge.tools.derive_idempotency_key` and the request hash
via ``sha256(canonical_args_json(args))``. Same key + same hash means
the model retried the same call; we return the cached observation
without re-dispatching. Same key + different hash means the canonical
args differ — the model emitted a contradictory request and the loop
surfaces this as a conflict rather than silently overwriting state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agentforge.persistence.models import IdempotencyKeyRow


class IdempotencyConflictError(RuntimeError):
    """Raised when an idempotency key collides with a different request.

    The same ``(session_id, tool_name, step)`` was reused with different
    canonical args. The executor surfaces this as a typed observation
    back to the model so it can reconcile or escalate.
    """


@dataclass
class CachedObservation:
    """An idempotency-cached observation, re-hydrated from the DB row."""

    key: str
    tool_name: str
    request_hash: str
    payload: dict[str, Any]
    created_at: datetime


class IdempotencyStore:
    """Thin SQLAlchemy repository over the ``idempotency_keys`` table."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, key: str) -> CachedObservation | None:
        row = self.db.get(IdempotencyKeyRow, key)
        if row is None:
            return None
        return CachedObservation(
            key=row.key,
            tool_name=row.tool_name,
            request_hash=row.request_hash,
            payload=json.loads(row.response_json),
            created_at=row.created_at,
        )

    def put(
        self,
        *,
        key: str,
        tool_name: str,
        request_hash: str,
        response_payload: dict[str, Any],
    ) -> CachedObservation:
        """Store an observation for ``key``.

        If ``key`` already exists with the same ``request_hash``, return
        the cached entry untouched (idempotent). If the ``request_hash``
        differs, raise :class:`IdempotencyConflictError`.
        """
        existing = self.db.get(IdempotencyKeyRow, key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError(
                    f"idempotency key {key[:12]}… already used with a "
                    f"different request_hash "
                    f"(was {existing.request_hash[:12]}…, "
                    f"now {request_hash[:12]}…)"
                )
            return CachedObservation(
                key=existing.key,
                tool_name=existing.tool_name,
                request_hash=existing.request_hash,
                payload=json.loads(existing.response_json),
                created_at=existing.created_at,
            )

        row = IdempotencyKeyRow(
            key=key,
            tool_name=tool_name,
            request_hash=request_hash,
            response_json=json.dumps(response_payload, separators=(",", ":")),
            created_at=datetime.now(UTC),
        )
        self.db.add(row)
        self.db.flush()
        return CachedObservation(
            key=row.key,
            tool_name=row.tool_name,
            request_hash=row.request_hash,
            payload=response_payload,
            created_at=row.created_at,
        )


def request_hash_for(canonical_args: str) -> str:
    """Hash for the ``request_hash`` column.

    Distinct from the idempotency key (which mixes in ``session_id``,
    ``tool_name``, and ``step``); this hash answers "are the canonical
    args identical?" for the conflict check.
    """
    return hashlib.sha256(canonical_args.encode("utf-8")).hexdigest()
