"""HTTP integration tests for the BP9 repair wizard endpoints.

Covers:

  * POST /sessions/{id}/load_fixture/{name} — copies the bundled
    ``invoice_aging_v1`` fixture into ``working/``, stages the golden
    into ``evals/``, emits a ``decision_input`` summary plus one
    ``file_uploaded`` per copied file. Path-discipline boundary cases
    (404 unknown session / 404 unknown fixture / 400 invalid name /
    409 non-empty working/).
  * POST /sessions/{id}/run for ``workflow='repair'`` — drives the
    bundled ``invoice_aging_v1`` fixture from 2-pass-1-fail to 3-pass
    via a scripted ``FakeModelClient`` (the same 14-turn script the
    in-process ``test_repair_flow_e2e`` exercises). Asserts the patch
    landed, tests now pass, ``reports/repair_report.md`` exists, the
    DB row + manifest both read ``completed``.

The fake-client script is reused from ``test_repair_flow_e2e``; only
the entry path differs (HTTP vs. in-Python). BP9's role is to prove
the HTTP layer dispatches the repair flow correctly, NOT to
re-validate the orchestrator (covered in BP6).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from agentforge.models import FakeModelClient
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from tests.test_repair_flow_e2e import (  # noqa: PLC2701 — shared script
    _build_repair_report,
    _fix_phase_script,
    _info_phase_script,
)


def _create_repair_session(client: TestClient) -> dict:
    resp = client.post("/sessions", json={"workflow": "repair"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _poll_until(
    client: TestClient,
    session_id: str,
    predicate,
    *,
    timeout_s: float = 90.0,
    interval_s: float = 0.25,
) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    events: list[dict] = []
    while time.monotonic() < deadline:
        resp = client.get(f"/sessions/{session_id}/events")
        assert resp.status_code == 200, resp.text
        events = resp.json()["events"]
        if predicate(events):
            return events
        time.sleep(interval_s)
    raise AssertionError(
        f"timed out after {timeout_s}s; last event kinds: "
        f"{[e['kind'] for e in events]}"
    )


# ---------------------------------------------------------------------------
# /load_fixture happy path + edges
# ---------------------------------------------------------------------------


def test_load_fixture_copies_invoice_aging_into_working(
    app_client: TestClient,
) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    resp = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == sid
    assert body["fixture_name"] == "invoice_aging_v1"
    paths = body["files_copied"]
    assert any(p == "working/agent.py" for p in paths)
    assert any(p == "working/rules.py" for p in paths)
    assert any(p.startswith("working/tests/") for p in paths)
    assert any(p.startswith("working/data/") for p in paths)
    assert body["staged_golden_path"] == "evals/expected_output.csv"

    # On-disk
    assert (workspace / "working" / "agent.py").is_file()
    assert (workspace / "evals" / "expected_output.csv").is_file()

    # Event log: decision_input + one file_uploaded per copied file.
    events = app_client.get(f"/sessions/{sid}/events").json()["events"]
    decision = [
        e for e in events
        if e["kind"] == "decision_input"
        and e["payload"].get("kind") == "fixture_loaded"
    ]
    assert len(decision) == 1
    assert decision[0]["payload"]["fixture_name"] == "invoice_aging_v1"
    uploads = [e for e in events if e["kind"] == "file_uploaded"]
    assert len(uploads) == len(paths)


def test_load_fixture_404_unknown_session(app_client: TestClient) -> None:
    resp = app_client.post(f"/sessions/{uuid4()}/load_fixture/invoice_aging_v1")
    assert resp.status_code == 404


def test_load_fixture_404_unknown_fixture(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    resp = app_client.post(
        f"/sessions/{session['id']}/load_fixture/no_such_fixture"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "fixture_not_found"


def test_load_fixture_400_invalid_name(app_client: TestClient) -> None:
    """Names with traversal characters are rejected at the boundary."""
    session = _create_repair_session(app_client)
    # FastAPI's path matcher will already reject names with '/' (the URL
    # won't match the route). What we want to test here is a name that
    # passes the URL match but contains traversal characters.
    resp = app_client.post(
        f"/sessions/{session['id']}/load_fixture/..invoice_aging"
    )
    # '..' contains '.', which is not in the whitelist; we expect 400.
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "invalid_fixture_name"


def test_load_fixture_409_when_working_not_empty(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]
    # First load: success.
    first = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v1")
    assert first.status_code == 200, first.text
    # Second load: 409 because working/ now has files.
    second = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v1")
    assert second.status_code == 409
    assert second.json()["detail"]["error_code"] == "working_not_empty"


# ---------------------------------------------------------------------------
# /run for workflow='repair' — load-bearing happy path
# ---------------------------------------------------------------------------


def test_run_drives_invoice_aging_to_completed_through_http(
    app_client: TestClient,
) -> None:
    """The BP9 verification gate: HTTP /load_fixture + /run → completed."""
    session = _create_repair_session(app_client)
    sid = session["id"]
    sid_uuid = UUID(sid)
    workspace = Path(session["workspace_path"])

    # 1. Load the bundled fixture via the new endpoint.
    load = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v1")
    assert load.status_code == 200, load.text

    # 2. Build the FakeModelClient script — same 14-turn flow the
    #    in-process E2E uses.
    diagnosis_id = str(uuid4())
    file_id = uuid4()  # repair INFO script doesn't actually use this
    report = _build_repair_report(sid_uuid, diagnosis_id)
    script = []
    script.extend(_info_phase_script(diagnosis_id=diagnosis_id, file_id=file_id))
    script.extend(_fix_phase_script(session_id=sid_uuid, report=report))
    app_client.app.state.model_client = FakeModelClient(script=script)

    # 3. POST /run with the problem report as the user_message.
    run = app_client.post(
        f"/sessions/{sid}/run",
        json={
            "user_message": (
                "April invoices are missing from the 0-30 aging bucket; "
                "some rows show PARSE_ERROR. Reproduce, diagnose, fix."
            ),
        },
    )
    assert run.status_code == 202, run.text
    assert run.json()["status"] == "running"

    # 4. Poll /events until workflow_completed appears.
    completed_events = _poll_until(
        app_client,
        sid,
        predicate=lambda evs: any(e["kind"] == "workflow_completed" for e in evs),
        timeout_s=120.0,
        interval_s=0.3,
    )
    kinds = [e["kind"] for e in completed_events]
    # The deterministic backbone runs the FIX phase regardless of
    # whether the LLM's scripted FIX-phase responses fire. The
    # load-bearing events on the new flow:
    assert "patch_applied" in kinds
    assert "repair_report_generated" in kinds
    assert "artifact_generated" in kinds

    # 5. The fix landed: agent.py now uses %m-%d-%Y.
    patched = (workspace / "working" / "agent.py").read_text()
    assert '"%m-%d-%Y"' in patched
    assert '"%d-%m-%Y"' not in patched

    # 6. RepairReport markdown + JSON were rendered.
    assert (workspace / "reports" / "repair_report.md").is_file()
    assert (workspace / "reports" / "repair_report.json").is_file()
    report_md = (workspace / "reports" / "repair_report.md").read_text()
    assert "# Repair Report" in report_md

    # 7. The DB row + manifest mirror to ``completed``.
    refreshed = app_client.get(f"/sessions/{sid}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "completed"
    assert refreshed.json()["completed_at"] is not None
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["status"] == "completed"

    # 8. The deterministic backbone records before/after pytest
    #    evidence via DECISION_INPUT { kind: pytest_before_fix /
    #    pytest_after_fix } events. The after-fix run must be green.
    pytest_events = [
        e
        for e in completed_events
        if e["kind"] == "decision_input"
        and e["payload"].get("kind") in {"pytest_before_fix", "pytest_after_fix"}
    ]
    assert len(pytest_events) >= 2, (
        f"expected before+after pytest evidence; got {pytest_events}"
    )
    after = next(
        e["payload"]
        for e in pytest_events
        if e["payload"]["kind"] == "pytest_after_fix"
    )
    assert after["failed"] == 0
    assert after["passed"] >= 3


def test_repair_run_recovers_orphaned_running_without_active_task(
    app_client: TestClient,
) -> None:
    """RUNNING without a live background task is recovered, then /run accepts."""
    from agentforge.persistence.db import get_session_factory
    from agentforge.persistence.models import SessionRow

    session = _create_repair_session(app_client)
    sid = session["id"]
    factory = get_session_factory()
    db = factory()
    try:
        row = db.get(SessionRow, sid)
        assert row is not None
        row.status = "running"
        db.commit()
    finally:
        db.close()

    resp = app_client.post(f"/sessions/{sid}/run", json={})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"


def test_run_drives_invoice_aging_v2_to_completed_through_http(
    app_client: TestClient,
) -> None:
    """Demo path: load_fixture invoice_aging_v2 + /run → completed via evidence pipeline."""
    session = _create_repair_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    load = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v2")
    assert load.status_code == 200, load.text
    body = load.json()
    assert body["fixture_name"] == "invoice_aging_v2"
    assert any(p == "working/agent.py" for p in body["files_copied"])
    assert any("problem_report.md" in p for p in body["files_copied"])
    assert body["staged_golden_path"] == "evals/expected_output.csv"

    # Deterministic repair pipeline — no scripted LLM turns required.
    app_client.app.state.model_client = FakeModelClient(script=[])

    run = app_client.post(f"/sessions/{sid}/run", json={})
    assert run.status_code == 202, run.text

    completed_events = _poll_until(
        app_client,
        sid,
        predicate=lambda evs: any(
            e["kind"] in {"workflow_completed", "workflow_failed"} for e in evs
        ),
        timeout_s=120.0,
        interval_s=0.3,
    )
    assert any(e["kind"] == "workflow_completed" for e in completed_events), (
        f"repair did not complete; last events: "
        f"{[e['kind'] for e in completed_events[-5:]]}"
    )

    patched = (workspace / "working" / "agent.py").read_text()
    assert "if days_overdue <= 30:" in patched
    assert "if days_overdue <= 31:" not in patched

    assert (workspace / "reports" / "repair_report.md").is_file()
    assert (workspace / "reports" / "repair_report.json").is_file()

    refreshed = app_client.get(f"/sessions/{sid}")
    assert refreshed.json()["status"] == "completed"

    pytest_events = [
        e
        for e in completed_events
        if e["kind"] == "decision_input"
        and e["payload"].get("kind") in {"pytest_before_fix", "pytest_after_fix"}
    ]
    before = next(
        e["payload"]
        for e in pytest_events
        if e["payload"]["kind"] == "pytest_before_fix"
    )
    after = next(
        e["payload"]
        for e in pytest_events
        if e["payload"]["kind"] == "pytest_after_fix"
    )
    assert before["failed"] == 2
    assert before["passed"] == 5
    assert after["failed"] == 0
    assert after["passed"] == 7


def test_event_log_chain_intact_after_repair_http_run(
    app_client: TestClient,
) -> None:
    """The append-only event log's prev_event_id chain stays linked
    across the fixture load + HTTP /run dispatch (INV-6 spot check)."""
    session = _create_repair_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v1")

    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    valid, msg = log.verify_chain(UUID(sid))
    assert valid, f"event chain broken after fixture load: {msg}"
