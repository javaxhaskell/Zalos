"""Architecture invariant tests — event log, reserved paths, repair bounds.

Natural regression scaffolding for load-bearing boundaries documented in
docs/sandbox-and-artifacts.md and docs/failure-modes.md.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentforge.config import get_settings


def test_scan_detects_author_output_contract_write() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = (
        "contract_path = 'generated/author_output_contract.json'\n"
        "open(contract_path, 'w').write('{}')\n"
    )
    assert "write to reserved path: generated/author_output_contract.json" in _scan_model_code(
        source
    )


def test_read_all_raises_on_malformed_event_line(tmp_path) -> None:
    from agentforge.persistence.event_log import EventLog, EventLogError
    from agentforge.persistence.workspace import WorkspaceManager
    from agentforge.schemas import ActorType, EventKind, Workflow

    wm = WorkspaceManager(root=tmp_path)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    log = EventLog(wm)
    log.append(
        session_id=sid,
        kind=EventKind.WORKSPACE_ALLOCATED,
        actor_type=ActorType.SYSTEM,
        payload={"note": "seed"},
    )
    events_path = wm.get(sid) / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8") + '{"not_a_workspace_event": true}\n',
        encoding="utf-8",
    )
    with pytest.raises(EventLogError, match="failed to parse"):
        log.read_all(sid)


def test_verify_chain_detects_broken_prev_event_id(tmp_path) -> None:
    from agentforge.persistence.event_log import EventLog
    from agentforge.persistence.workspace import WorkspaceManager
    from agentforge.schemas import ActorType, EventKind, Workflow

    wm = WorkspaceManager(root=tmp_path)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    log = EventLog(wm)
    first = log.append(
        session_id=sid,
        kind=EventKind.WORKSPACE_ALLOCATED,
        actor_type=ActorType.SYSTEM,
        payload={"note": "first"},
    )
    second = log.append(
        session_id=sid,
        kind=EventKind.WORKFLOW_STARTED,
        actor_type=ActorType.SYSTEM,
        payload={"workflow": "author"},
    )
    assert second.prev_event_id == first.id

    # Simulate chain break by rewriting the second line with a wrong prev_event_id.
    events = log.read_all(sid)
    broken = events[1].model_copy(update={"prev_event_id": uuid4()})
    path = wm.get(sid) / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = broken.model_dump_json()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    valid, msg = log.verify_chain(sid)
    assert valid is False
    assert msg is not None
    assert "prev_event_id" in msg


def test_author_max_repair_attempts_default_is_bounded() -> None:
    settings = get_settings()
    assert settings.author_max_repair_attempts >= 1
    assert settings.author_max_repair_attempts <= 5


def test_reserved_paths_include_audit_control_files() -> None:
    from agentforge.orchestrator.author_llm_authoring import _BACKEND_MANAGED_ARTIFACT_PATHS

    required = {
        "events.jsonl",
        "manifest.json",
        "archive.zip",
        "generated/author_output_contract.json",
    }
    assert required.issubset(_BACKEND_MANAGED_ARTIFACT_PATHS)
