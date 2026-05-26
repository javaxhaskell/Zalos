"""Per-session ``events.jsonl`` — append-only event log (INV-6).

All session audit writes go through :meth:`EventLog.append`. No other module
opens the file for write. :meth:`read_all` and :meth:`_last_event_id` raise
:class:`EventLogError` on malformed lines so corrupted tails (e.g. reserved-path
overwrites by generated agents) fail closed instead of breaking the chain silently.
See docs/sandbox-and-artifacts.md.

One JSON event per line. Events are chained via ``prev_event_id`` so the
chronology is reconstructable even if rows are read out-of-order. The
storage layer enforces append-only by convention (INV-6); the file is
never opened for write/truncate.

Note: this layer does NOT include the integrity-hash chain described as a
production extension in ARCHITECTURE.md. For the prototype,
``prev_event_id`` linkage + the file's append-only convention is sufficient.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind, WorkspaceEvent


class EventLogError(Exception):
    """Raised on event-log integrity violations."""


class EventLog:
    """Append + read the per-session ``events.jsonl`` file.

    All writes go through ``append``; nothing else opens the file for
    write. Reads are streaming via ``read_all`` / ``read_since``.
    """

    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self.wm = workspace_manager

    # ------------------------------------------------------------------
    # Locate
    # ------------------------------------------------------------------

    def _path(self, session_id: UUID) -> Path:
        return self.wm.get(session_id) / "events.jsonl"

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        session_id: UUID,
        kind: EventKind,
        actor_type: ActorType,
        payload: Any = None,
        step: int = 0,
        ts: datetime | None = None,
    ) -> WorkspaceEvent:
        """Append a single typed event to ``events.jsonl``.

        ``payload`` may be a Pydantic model (auto-serialised via
        ``model_dump(mode='json')``), a plain JSON-safe ``dict``, or
        ``None`` (becomes ``{}``). ``prev_event_id`` is derived from the
        last event already on disk.
        """
        if payload is None:
            payload_dict: dict[str, Any] = {}
        elif hasattr(payload, "model_dump"):
            payload_dict = payload.model_dump(mode="json")
        elif isinstance(payload, dict):
            payload_dict = payload
        else:
            raise TypeError(
                f"payload must be a Pydantic model, dict, or None; got {type(payload).__name__}"
            )

        prev = self._last_event_id(session_id)

        event = WorkspaceEvent(
            id=uuid4(),
            session_id=session_id,
            ts=ts or datetime.now(UTC),
            step=step,
            kind=kind,
            actor_type=actor_type,
            payload=payload_dict,
            prev_event_id=prev,
        )

        line = event.model_dump_json()
        path = self._path(session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
            f.flush()

        return event

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_all(self, session_id: UUID) -> list[WorkspaceEvent]:
        """Return all events for a session, ordered by file position."""
        path = self._path(session_id)
        events: list[WorkspaceEvent] = []
        if not path.exists():
            return events
        with path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    events.append(WorkspaceEvent.model_validate_json(line))
                except Exception as exc:  # noqa: BLE001
                    raise EventLogError(
                        f"{path.name}:{line_no} failed to parse: {exc}"
                    ) from exc
        return events

    def read_since(
        self, session_id: UUID, after_event_id: UUID | None
    ) -> list[WorkspaceEvent]:
        """Return events strictly after ``after_event_id``.

        If ``after_event_id`` is None, returns all events. If the id is not
        found in the log, returns an empty list (caller may interpret that
        as "no new events" and continue polling).
        """
        all_events = self.read_all(session_id)
        if after_event_id is None:
            return all_events
        target = str(after_event_id)
        for idx, evt in enumerate(all_events):
            if str(evt.id) == target:
                return all_events[idx + 1 :]
        return []

    # ------------------------------------------------------------------
    # Chain integrity (lightweight)
    # ------------------------------------------------------------------

    def verify_chain(self, session_id: UUID) -> tuple[bool, str | None]:
        """Walk the chain and confirm each event's ``prev_event_id`` matches the previous event's id.

        Returns ``(valid, first_break_message)``. The first event must have
        ``prev_event_id`` of ``None``.
        """
        events = self.read_all(session_id)
        if not events:
            return (True, None)
        if events[0].prev_event_id is not None:
            return (False, f"first event {events[0].id} has prev_event_id != None")
        for i in range(1, len(events)):
            expected = events[i - 1].id
            actual = events[i].prev_event_id
            if actual != expected:
                return (
                    False,
                    f"event {events[i].id} prev_event_id={actual} but expected {expected}",
                )
        return (True, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _last_event_id(self, session_id: UUID) -> UUID | None:
        """Return the id of the last event in the file, or None if empty."""
        path = self._path(session_id)
        if not path.exists() or path.stat().st_size == 0:
            return None
        # Stream the file once. For prototype scale (< 1k events/session)
        # this is fast enough; production swap is an indexed cache.
        last_line: str | None = None
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line_stripped = line.strip()
                if line_stripped:
                    last_line = line_stripped
        if not last_line:
            return None
        try:
            parsed = json.loads(last_line)
        except json.JSONDecodeError as exc:
            raise EventLogError(
                f"{path.name} last line is not valid JSON: {exc}"
            ) from exc
        event_id = parsed.get("id")
        if not event_id:
            preview = last_line[:240]
            raise EventLogError(
                f"{path.name} last line is missing required event id; "
                f"events.jsonl may have been overwritten by generated code or "
                f"another non-audit writer. line_preview={preview!r}"
            )
        try:
            return UUID(str(event_id))
        except (TypeError, ValueError) as exc:
            raise EventLogError(
                f"{path.name} last line has invalid event id {event_id!r}"
            ) from exc
