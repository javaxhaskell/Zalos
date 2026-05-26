"""Repair primary-problem precedence and mismatch handling."""

from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import FakeModelClient
from agentforge.orchestrator import RepairFlow
from agentforge.orchestrator.repair_problem import resolve_primary_problem
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind, SessionStatus, Workflow
from agentforge.tools import build_registry
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[3]
_V2_FIXTURE = _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v2"


@pytest.fixture()
def e2e_db(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'e2e.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _stage_fixture(workspace: Path, fixture: Path) -> None:
    working = workspace / "working"
    working.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        fixture,
        working / fixture.name,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".DS_Store", "*.pyc"
        ),
    )


def _record_fixture_loaded(event_log: EventLog, session_id, fixture_name: str) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={"kind": "fixture_loaded", "fixture_name": fixture_name, "file_count": 1},
        step=0,
    )


def _record_user_problem(event_log: EventLog, session_id, text: str) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={"kind": "repair_user_problem", "text": text},
        step=0,
    )


def _record_zip_upload(event_log: EventLog, session_id) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={
            "kind": "agent_zip_uploaded",
            "archive_filename": "broken_agent.zip",
            "file_count": 1,
        },
        step=0,
    )


def _make_flow(workspaces_root: Path, db, model_client: FakeModelClient):
    wm = WorkspaceManager(root=workspaces_root)
    event_log = EventLog(wm)
    loop = AgentLoop(
        registry=build_registry(),
        model_client=model_client,
        idempotency_store=IdempotencyStore(db=db),
        event_log=event_log,
        workspace_manager=wm,
        settings=get_settings(),
    )
    flow = RepairFlow(
        agent_loop=loop,
        event_log=event_log,
        workspace_manager=wm,
        settings=get_settings(),
    )
    return wm, event_log, flow


async def _run_v2_repair(
    workspaces_root: Path,
    e2e_db,
    *,
    fixture_loaded: bool = True,
    zip_uploaded: bool = False,
    user_problem: str | None = None,
):
    wm, event_log, flow = _make_flow(workspaces_root, e2e_db, FakeModelClient(script=[]))
    sid = uuid4()
    wm.allocate(sid, Workflow.REPAIR)
    workspace = wm.get(sid)
    _stage_fixture(workspace, _V2_FIXTURE)
    if fixture_loaded:
        _record_fixture_loaded(event_log, sid, "invoice_aging_v2")
    if zip_uploaded:
        _record_zip_upload(event_log, sid)
    if user_problem:
        _record_user_problem(event_log, sid, user_problem)

    outcome = await flow.run(
        session_id=sid,
        system_prompt="repair agent",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=180),
    )
    return wm, event_log, sid, workspace, outcome


@pytest.mark.asyncio
async def test_builtin_sample_without_user_problem_uses_embedded_report(
    workspaces_root: Path,
    e2e_db,
) -> None:
    wm, event_log, sid, workspace, outcome = await _run_v2_repair(
        workspaces_root, e2e_db, fixture_loaded=True, user_problem=None
    )
    assert outcome.terminal_status == SessionStatus.COMPLETED

    report = json.loads((workspace / "reports" / "repair_report.json").read_text())
    assert report["primary_problem"]["source"] == "built_in_sample_problem_report"
    assert "31 days overdue" in report["primary_problem"]["text"]

    resolved = resolve_primary_problem(
        session_id=sid,
        working_dir=workspace / "working" / "invoice_aging_v2",
        event_log=event_log,
    )
    assert resolved.source == "built_in_sample_problem_report"


@pytest.mark.asyncio
async def test_uploaded_zip_without_user_problem_uses_uploaded_report(
    workspaces_root: Path,
    e2e_db,
) -> None:
    wm, event_log, sid, workspace, outcome = await _run_v2_repair(
        workspaces_root,
        e2e_db,
        fixture_loaded=False,
        zip_uploaded=True,
        user_problem=None,
    )
    assert outcome.terminal_status == SessionStatus.COMPLETED
    report = json.loads((workspace / "reports" / "repair_report.json").read_text())
    assert report["primary_problem"]["source"] == "uploaded_problem_report"


@pytest.mark.asyncio
async def test_user_typed_problem_overrides_embedded_report(
    workspaces_root: Path,
    e2e_db,
) -> None:
    custom = (
        "Invoices that are exactly 31 days overdue are showing up in the "
        "1-30 aging bucket instead of 31-60."
    )
    wm, event_log, sid, workspace, outcome = await _run_v2_repair(
        workspaces_root,
        e2e_db,
        fixture_loaded=False,
        zip_uploaded=True,
        user_problem=custom,
    )
    assert outcome.terminal_status == SessionStatus.COMPLETED
    report = json.loads((workspace / "reports" / "repair_report.json").read_text())
    assert report["primary_problem"]["source"] == "user_input"
    assert custom in report["primary_problem"]["text"]
    assert any(
        "did not override user input" in note
        for note in (report.get("problem_notes") or [])
    )


@pytest.mark.asyncio
async def test_conflicting_user_problem_does_not_complete_silently(
    workspaces_root: Path,
    e2e_db,
) -> None:
    wm, event_log, sid, workspace, outcome = await _run_v2_repair(
        workspaces_root,
        e2e_db,
        fixture_loaded=True,
        user_problem="Paid invoices are being placed into overdue buckets.",
    )
    assert outcome.terminal_status != SessionStatus.COMPLETED
    assert outcome.terminal_error_code is not None

    events = event_log.read_all(sid)
    failed = next(e for e in events if e.kind == EventKind.WORKFLOW_FAILED)
    assert failed.payload["error_code"] == "repair_cannot_reproduce"
    assert "discovered_issue_summary" in failed.payload
    assert not (workspace / "reports" / "repair_report.json").is_file()


def test_zip_upload_without_user_problem_via_http(app_client: TestClient) -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _V2_FIXTURE.rglob("*"):
            if path.is_file():
                rel = path.relative_to(_V2_FIXTURE)
                zf.write(path, f"invoice_aging_v2/{rel.as_posix()}")
    buf.seek(0)

    session = app_client.post("/sessions", json={"workflow": "repair"}).json()
    sid = session["id"]
    upload = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("invoice_aging_v2.zip", buf.getvalue(), "application/zip")},
    )
    assert upload.status_code == 200, upload.text

    app_client.app.state.model_client = FakeModelClient(script=[])
    run = app_client.post(f"/sessions/{sid}/run", json={})
    assert run.status_code == 202, run.text

    import time

    deadline = time.monotonic() + 120
    events: list[dict] = []
    while time.monotonic() < deadline:
        events = app_client.get(f"/sessions/{sid}/events").json()["events"]
        if any(e["kind"] == "workflow_completed" for e in events):
            break
        if any(
            e["kind"] == "workflow_failed"
            and e["payload"].get("error_code") == "repair_cannot_reproduce"
            for e in events
        ):
            break
        time.sleep(0.25)

    workspace = Path(session["workspace_path"])
    report_path = workspace / "reports" / "repair_report.json"
    assert report_path.is_file(), events[-3:]
    report = json.loads(report_path.read_text())
    assert report["primary_problem"]["source"] == "uploaded_problem_report"
