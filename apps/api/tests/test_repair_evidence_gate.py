"""Evidence-gate regression for the Repair workflow.

The previous design let the LLM-driven flow complete without
producing artifacts, so the UI could (and did) lie: "Repair complete"
when no `reports/repair_report.md` existed and no patch had been
applied. These tests lock the new behaviour in:

* On the **invoice_aging_v2** fixture (boundary bug), the
  evidence pipeline reproduces the failure, derives a patch proposal
  from failing tests + source, applies it after validation, re-runs
  pytest, and writes a real repair report.

* If the broken agent's bug cannot be inferred from evidence,
  the workflow MUST NOT complete — it surfaces a `workflow_failed`
  event so the UI can show "Repair incomplete" honestly.

* Fixture / ZIP staging strips `.pytest_cache`, `__pycache__`,
  `*.pyc`, `.DS_Store`, `CACHEDIR.TAG`, `lastfailed`, `nodeids`.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import FakeModelClient
from agentforge.orchestrator import RepairFlow
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind, SessionStatus, Workflow
from agentforge.tools import build_registry

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
    """Copy a fixture into ``working/<name>/`` (matches how the load
    endpoint stages a fixture)."""
    working = workspace / "working"
    working.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        fixture,
        working / fixture.name,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".DS_Store", "*.pyc"
        ),
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


async def test_invoice_aging_v2_repair_produces_full_evidence_chain(
    workspaces_root: Path,
    e2e_db,
) -> None:
    """The v2 fixture must go from 5/2 → 7/0 with every audit artifact."""
    wm, event_log, flow = _make_flow(workspaces_root, e2e_db, FakeModelClient(script=[]))
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)  # author or repair — the workspace shape is identical
    workspace = wm.get(sid)
    _stage_fixture(workspace, _V2_FIXTURE)
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={
            "kind": "fixture_loaded",
            "fixture_name": "invoice_aging_v2",
            "file_count": 1,
        },
        step=0,
    )

    outcome = await flow.run(
        session_id=sid,
        system_prompt="repair agent",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=180),
    )

    assert outcome.terminal_status == SessionStatus.COMPLETED, (
        f"unexpected terminal status: {outcome.terminal_status}\n"
        f"info_outcome={outcome.info_outcome}"
    )

    # ----- Patched on disk -----
    patched = (workspace / "working" / "invoice_aging_v2" / "agent.py").read_text()
    assert "if days_overdue <= 30:" in patched
    assert "if days_overdue <= 31:" not in patched
    assert "BOUNDARY BUG" not in patched
    assert "Correct boundary:" in patched
    assert "Invoices exactly 31 days overdue belong in the 31-60 bucket." in patched
    assert "ships with a deliberate boundary bug" not in patched
    assert "boundary bug corrected" in patched
    assert "original fixture contained a deliberate boundary bug" in patched

    # ----- repair_report.md + .json materialised -----
    md_path = workspace / "reports" / "repair_report.md"
    json_path = workspace / "reports" / "repair_report.json"
    assert md_path.is_file()
    assert json_path.is_file()
    md = md_path.read_text()
    for heading in (
        "## Problem reported",
        "## Files inspected",
        "## Failure reproduced",
        "## Root cause",
        "## Fix applied",
        "## Changed files",
        "## Validation evidence",
        "## Remaining risks",
    ):
        assert heading in md, f"missing heading: {heading}"

    report_json = json.loads(json_path.read_text())
    assert report_json["primary_problem"]["source"] == "built_in_sample_problem_report"
    assert "31 days overdue" in report_json["primary_problem"]["text"]
    inspected = report_json.get("files_inspected") or []
    forbidden_segments = (
        ".pytest_cache",
        "__pycache__",
        ".DS_Store",
        "CACHEDIR.TAG",
        "lastfailed",
        "nodeids",
    )
    for path in inspected:
        for seg in forbidden_segments:
            assert seg not in path, f"junk listed in files_inspected: {path!r}"
        assert not path.endswith(".pyc"), f"junk listed in files_inspected: {path!r}"
    assert any("agent.py" in p for p in inspected)
    assert any("test_agent.py" in p for p in inspected)

    # ----- Before/after pytest evidence persisted as artifacts -----
    events = event_log.read_all(sid)
    artifact_types = {
        e.payload.get("artifact_type")
        for e in events
        if e.kind == EventKind.ARTIFACT_GENERATED
    }
    for required in (
        "pytest_before_fix_log",
        "pytest_after_fix_log",
        "patch_diff",
        "repair_report",
        "repair_report_json",
    ):
        assert required in artifact_types, f"missing artifact: {required}"

    # ----- pytest evidence numbers -----
    pytest_events = [
        e
        for e in events
        if e.kind == EventKind.DECISION_INPUT
        and e.payload.get("kind") in {"pytest_before_fix", "pytest_after_fix"}
    ]
    before = next(e for e in pytest_events if e.payload["kind"] == "pytest_before_fix")
    after = next(e for e in pytest_events if e.payload["kind"] == "pytest_after_fix")
    assert before.payload["failed"] >= 2, before.payload
    assert before.payload["passed"] >= 5, before.payload
    assert after.payload["failed"] == 0, after.payload
    assert after.payload["passed"] == 7, after.payload

    # ----- workflow_completed payload has real values -----
    completed = next(e for e in events if e.kind == EventKind.WORKFLOW_COMPLETED)
    payload = completed.payload
    assert payload["via"] == "repair_validated_patch"
    assert payload["summary"] == "Tests passed after repair."
    assert payload["repair_reproduced"] is True
    assert payload["patch_applied"] is True
    assert payload["post_fix_tests_passed"] is True

    manifest = json.loads((workspace / "manifest.json").read_text())
    completion = manifest.get("completion")
    assert completion is not None, "manifest.completion must be populated on success"
    assert completion["completion_via"] == "repair_validated_patch"
    assert completion["repair_reproduced"] is True
    assert completion["patch_applied"] is True
    assert completion["post_fix_tests_passed"] is True
    assert completion["changed_files"]
    assert "agent.py" in completion["changed_files"][0]
    assert completion["before_fix_pytest_summary"]
    assert completion["after_fix_pytest_summary"]
    assert completion["repair_report_path"] == "reports/repair_report.md"
    assert completion["repair_report_json_path"] == "reports/repair_report.json"
    assert completion["patch_diff_path"]
    assert completion["before_fix_log_path"] == "reports/before_fix_pytest_output.txt"
    assert completion["after_fix_log_path"] == "reports/after_fix_pytest_output.txt"
    artifact_paths = {a["path"] for a in completion.get("artifacts") or []}
    for required in (
        "reports/before_fix_pytest_output.txt",
        "reports/after_fix_pytest_output.txt",
        "reports/agent_py.patch",
        "reports/repair_report.md",
        "reports/repair_report.json",
    ):
        assert required in artifact_paths, f"missing manifest artifact: {required}"
    assert len(completion.get("artifacts") or []) == 5

    from agentforge.persistence.archive import build_archive

    build_archive(session_id=sid, workspace_manager=wm)
    readme = (workspace / "SESSION_README.md").read_text()
    assert "## How this was repaired" in readme
    assert "repair_validated_patch" in readme
    assert "cd working/invoice_aging_v2" in readme
    assert "python3 -m pytest tests/ -q" in readme
    assert "generated/agent.py" not in readme
    assert "outputs/output.csv" not in readme
    assert "UNKNOWN" not in readme

    expected_summary = (
        "This invoice-aging agent reads invoice rows, computes days overdue "
        "from due_date, assigns aging buckets, and adds a risk flag. The "
        "repaired copy now uses the correct 30-day boundary for the 1-30 bucket."
    )
    assert report_json.get("business_logic_summary") == expected_summary
    assert "boundary bug" not in md.lower()

    proposal_events = [
        e for e in events if e.payload.get("kind") == "repair_proposal"
    ]
    assert proposal_events, "expected repair_proposal decision_input"
    assert proposal_events[0].payload["source"] == "evidence_inference"

    patch_events = [e for e in events if e.kind == EventKind.PATCH_APPLIED]
    assert patch_events
    assert patch_events[0].payload.get("proposal_source") == "evidence_inference"
    # No raw template placeholders may leak into the payload.
    serialised = repr(payload)
    for placeholder in ("{failing_test}", "{via}", "{summary}", "{purpose}"):
        assert placeholder not in serialised


async def test_unknown_bug_shape_does_not_complete(
    workspaces_root: Path,
    e2e_db,
    tmp_path: Path,
) -> None:
    """An unknown-pattern broken agent must surface workflow_failed,
    NOT COMPLETED. The UI cannot say 'Repair complete' in this case."""
    wm, event_log, flow = _make_flow(workspaces_root, e2e_db, FakeModelClient(script=[]))
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    # Fabricate an agent with a bug shape the deterministic patcher
    # doesn't recognise. It must NOT silently complete.
    agent_dir = workspace / "working" / "mystery_agent"
    (agent_dir / "tests").mkdir(parents=True)
    (agent_dir / "agent.py").write_text(
        "def main():\n    return 1 / 0  # divide by zero — unknown shape\n",
        encoding="utf-8",
    )
    (agent_dir / "tests" / "__init__.py").write_text("")
    (agent_dir / "tests" / "test_main.py").write_text(
        "from agent import main\n\n"
        "def test_main_works() -> None:\n"
        "    assert main() == 42\n",
        encoding="utf-8",
    )

    outcome = await flow.run(
        session_id=sid,
        system_prompt="repair agent",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=180),
    )

    assert outcome.terminal_status != SessionStatus.COMPLETED, (
        "unknown bug shapes must not complete; got " f"{outcome.terminal_status}"
    )
    events = event_log.read_all(sid)
    assert any(e.kind == EventKind.WORKFLOW_FAILED for e in events), (
        "expected a workflow_failed event for an unrecoverable repair"
    )


def test_fixture_loader_strips_junk(app_client: TestClient) -> None:
    """A fixture copied into a session's workspace must not carry
    .pytest_cache, __pycache__, *.pyc, .DS_Store, CACHEDIR.TAG, etc."""
    session = app_client.post(
        "/sessions", json={"workflow": "repair"}
    ).json()
    sid = session["id"]
    resp = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v2")
    assert resp.status_code == 200, resp.text
    files = resp.json()["files_copied"]
    forbidden_segments = (
        ".pytest_cache",
        "__pycache__",
        ".DS_Store",
        "CACHEDIR.TAG",
        "lastfailed",
        "nodeids",
        ".mypy_cache",
        ".ruff_cache",
    )
    for path in files:
        for seg in forbidden_segments:
            assert seg not in path, f"junk leaked into workspace: {path!r}"
        assert not path.endswith(".pyc"), f"junk leaked into workspace: {path!r}"


def test_zip_upload_strips_junk(app_client: TestClient, tmp_path: Path) -> None:
    """A ZIP upload carrying .pytest_cache + .pyc + .DS_Store entries
    must extract only the legitimate files into ``working/``."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("agent_pkg/agent.py", "print('hello')\n")
        zf.writestr("agent_pkg/tests/test_x.py", "def test_x(): pass\n")
        # Junk that should never reach the workspace:
        zf.writestr("agent_pkg/.pytest_cache/CACHEDIR.TAG", "Signature: x")
        zf.writestr("agent_pkg/.pytest_cache/v/cache/lastfailed", "{}")
        zf.writestr("agent_pkg/.pytest_cache/v/cache/nodeids", "[]")
        zf.writestr("agent_pkg/__pycache__/agent.cpython-313.pyc", "junk")
        zf.writestr("agent_pkg/.DS_Store", "junk")
        zf.writestr("agent_pkg/agent.pyc", "junk")
    buf.seek(0)
    session = app_client.post(
        "/sessions", json={"workflow": "repair"}
    ).json()
    sid = session["id"]
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("broken_agent.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    files = resp.json()["files_extracted"]
    for path in files:
        assert ".pytest_cache" not in path
        assert "__pycache__" not in path
        assert ".DS_Store" not in path
        assert not path.endswith(".pyc")
    # Legitimate entries still present:
    assert any("agent.py" in f for f in files)
    assert any("test_x.py" in f for f in files)
