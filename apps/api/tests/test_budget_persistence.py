"""Tests for the BP11 budget-persistence path.

After a flow completes via the BP8 runner supervisor, the per-session
budget counters (``tokens_used`` / ``tool_calls_used`` / ``steps_used``
/ ``wall_seconds_used``) are flushed to the ``sessions`` row so a
post-run ``GET /sessions/{id}`` carries the final usage. These tests
exercise both the persistence method directly + the round-trip
through ``POST /sessions/{id}/run`` for the happy author path.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from agentforge.models import FakeModelClient
from agentforge.persistence.db import get_session_factory
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.live_budget_sync import (
    persist_live_session_budget,
    reset_live_budget_session_store,
    set_live_budget_session_store,
)
from agentforge.persistence.models import SessionRow
from agentforge.persistence.session_store import SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind
from tests.author_model_fixtures import model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"


# ---------------------------------------------------------------------------
# SessionStore.update_budget — direct test
# ---------------------------------------------------------------------------


def test_update_budget_writes_counters_to_row(app_client: TestClient) -> None:
    """``update_budget`` flushes only the counters the caller provided."""
    create = app_client.post("/sessions", json={"workflow": "author"})
    assert create.status_code == 201
    sid = UUID(create.json()["id"])
    workspace_path = Path(create.json()["workspace_path"])

    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(workspace_path.parent)
        # The store reuses the EventLog held by the request — for this
        # direct-call test we don't need it, so a minimal one will do.
        from agentforge.persistence.event_log import EventLog

        log = EventLog(workspace_manager=wm)
        store = SessionStore(db=db, workspace_manager=wm, event_log=log)

        updated = store.update_budget(
            sid,
            tokens_used=12_345,
            tool_calls_used=7,
            steps_used=4,
            wall_seconds_used=42,
        )
        assert updated.budget.tokens_used == 12_345
        assert updated.budget.tool_calls_used == 7
        assert updated.budget.steps_used == 4
        assert updated.budget.wall_seconds_used == 42

        # Partial update — only the named counters change.
        store.update_budget(sid, tokens_used=20_000)
        row = db.get(SessionRow, str(sid))
        assert row is not None
        assert row.tokens_used == 20_000
        assert row.tool_calls_used == 7  # untouched
        assert row.steps_used == 4
        assert row.wall_seconds_used == 42
    finally:
        db.close()


# ---------------------------------------------------------------------------
# /run round-trip — budget counters land on the row after flow completion
# ---------------------------------------------------------------------------


def test_run_persists_budget_counters_at_flow_termination(
    app_client: TestClient,
) -> None:
    """After the bank-categoriser flow completes via HTTP, the row's
    budget counters reflect the loop's accumulated usage."""
    create = app_client.post("/sessions", json={"workflow": "author"})
    sid = UUID(create.json()["id"])
    workspace = Path(create.json()["workspace_path"])

    # Upload sample
    sample = (_BANK_DIR / "data" / "sample_input.csv").read_bytes()
    upload = app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("sample_input.csv", io.BytesIO(sample), "text/csv")},
    )
    assert upload.status_code == 201
    _file_id = UUID(upload.json()["uploaded_file_id"])

    # Stage the golden (the runner doesn't auto-stage; mirrors the
    # BP8 happy-path test setup).
    import shutil

    evals_dir = workspace / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        _BANK_DIR / "data" / "golden_output.csv",
        evals_dir / "golden_output.csv",
    )

    script = model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )
    app_client.app.state.model_client = FakeModelClient(script=script)

    # Kick off and poll until completed.
    run = app_client.post(
        f"/sessions/{sid}/run",
        json={"user_message": "Categorise bank transactions from this CSV."},
    )
    assert run.status_code == 202

    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        resp = app_client.get(f"/sessions/{sid}")
        assert resp.status_code == 200
        if resp.json()["status"] == "completed":
            break
        time.sleep(0.3)
    else:
        raise AssertionError("session did not complete within 90s")

    body = resp.json()
    budget = body["budget"]
    # The invariant we care about here is that loop counters are
    # persisted across an Author run. LLM-first Author completion is
    # covered separately by the author completion-gate tests.
    assert budget["steps_used"] > 0, f"steps_used not persisted: {budget}"
    assert budget["tool_calls_used"] >= 0
    assert budget["tokens_used"] >= 0
    assert budget["wall_seconds_used"] >= 0

    # Manifest mirror still matches (BP8 invariant).
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["status"] == "completed"


# ---------------------------------------------------------------------------
# Failure path — terminal_error_code is written even when the flow fails
# ---------------------------------------------------------------------------


def test_failed_session_surfaces_terminal_error_code(
    app_client: TestClient,
) -> None:
    """A flow that raises mid-run → row status FAILED_OTHER + the
    ``terminal_error_code`` column is populated."""
    # Empty-script FakeModelClient raises on the first call.
    app_client.app.state.model_client = FakeModelClient(script=[])
    create = app_client.post("/sessions", json={"workflow": "author"})
    sid = create.json()["id"]
    run = app_client.post(f"/sessions/{sid}/run", json={})
    assert run.status_code == 202

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        resp = app_client.get(f"/sessions/{sid}")
        status = resp.json()["status"]
        if status.startswith("failed_"):
            break
        time.sleep(0.3)
    else:
        raise AssertionError("session did not fail within 30s")

    body = resp.json()
    # The flow's ModelClientError surfaces the loop's terminal_error_code.
    # Depending on the exact failure path this lands either via the
    # supervisor's exception handler (UNKNOWN) or the loop's own
    # terminal_error_code (also UNKNOWN for the FakeModelClient case).
    assert body["status"] in ("failed_other", "failed_model")
    assert body.get("terminal_error_code") is not None


# ---------------------------------------------------------------------------
# Live tick — model_called flushes tokens before flow termination
# ---------------------------------------------------------------------------


def test_model_called_updates_session_budget_during_run(
    app_client: TestClient,
) -> None:
    """Each ``MODEL_CALLED`` event should increment ``tokens_used`` on the row."""
    create = app_client.post("/sessions", json={"workflow": "author"})
    assert create.status_code == 201
    sid = UUID(create.json()["id"])
    workspace_path = Path(create.json()["workspace_path"])

    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(workspace_path.parent)
        log = EventLog(workspace_manager=wm)
        store = SessionStore(db=db, workspace_manager=wm, event_log=log)
        token = set_live_budget_session_store(store)
        try:
            log.append(
                session_id=sid,
                kind=EventKind.MODEL_CALLED,
                actor_type=ActorType.MODEL,
                payload={
                    "purpose": "contract_planning",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "total_tokens": 1500,
                    },
                },
                step=0,
            )
            persist_live_session_budget(session_id=sid, event_log=log)
            row = db.get(SessionRow, str(sid))
            assert row is not None
            assert row.tokens_used == 1500

            log.append(
                session_id=sid,
                kind=EventKind.MODEL_CALLED,
                actor_type=ActorType.MODEL,
                payload={
                    "purpose": "contract_review",
                    "usage": {
                        "input_tokens": 800,
                        "output_tokens": 200,
                        "total_tokens": 1000,
                    },
                },
                step=0,
            )
            persist_live_session_budget(session_id=sid, event_log=log)
            db.refresh(row)
            assert row.tokens_used == 2500
        finally:
            reset_live_budget_session_store(token)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Unused — placeholder for the future "live tick" test where the
# wizard derives counters from events without waiting for terminal
# persistence. Documented here so a future BP11 extension has a
# starting test name.
# ---------------------------------------------------------------------------


def test_budget_counters_default_to_zero_before_run(
    app_client: TestClient,
) -> None:
    """A freshly created session has zeroed counters until /run fires."""
    create = app_client.post("/sessions", json={"workflow": "author"})
    sid = create.json()["id"]
    body = app_client.get(f"/sessions/{sid}").json()
    budget = body["budget"]
    assert budget["tokens_used"] == 0
    assert budget["tool_calls_used"] == 0
    assert budget["steps_used"] == 0
    assert budget["wall_seconds_used"] == 0
    # Reasonable defaults for the limits (from Settings.budget_*).
    assert budget["tokens_limit"] > 0
    assert budget["steps_limit"] > 0


def test_budget_summary_aggregates_tokens_across_sessions(
    app_client: TestClient,
) -> None:
    """``GET /sessions/budget/summary`` sums ``tokens_used`` for all rows."""
    author = app_client.post("/sessions", json={"workflow": "author"})
    repair = app_client.post("/sessions", json={"workflow": "repair"})
    assert author.status_code == 201
    assert repair.status_code == 201
    author_id = UUID(author.json()["id"])
    repair_id = UUID(repair.json()["id"])

    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(Path(author.json()["workspace_path"]).parent)
        log = EventLog(workspace_manager=wm)
        store = SessionStore(db=db, workspace_manager=wm, event_log=log)
        store.update_budget(author_id, tokens_used=10_000)
        store.update_budget(repair_id, tokens_used=5_500)
    finally:
        db.close()

    resp = app_client.get("/sessions/budget/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lifetime_tokens_used"] == 15_500
    assert body["session_count"] >= 2

    by_id = {item["id"]: item for item in body["recent_sessions"]}
    assert by_id[str(author_id)]["tokens_used"] == 10_000
    assert by_id[str(repair_id)]["tokens_used"] == 5_500


def test_budget_summary_excludes_soft_deleted_sessions(
    app_client: TestClient,
) -> None:
    """Soft-deleted sessions do not contribute to lifetime totals."""
    create = app_client.post("/sessions", json={"workflow": "author"})
    sid = UUID(create.json()["id"])

    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(Path(create.json()["workspace_path"]).parent)
        log = EventLog(workspace_manager=wm)
        store = SessionStore(db=db, workspace_manager=wm, event_log=log)
        store.update_budget(sid, tokens_used=9_999)
        store.soft_delete_session(sid)
    finally:
        db.close()

    resp = app_client.get("/sessions/budget/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lifetime_tokens_used"] == 0
    assert all(item["id"] != str(sid) for item in body["recent_sessions"])


def _unused_anchor() -> UUID:
    """Anchor for the unused uuid4 import so future tests can grab it."""
    return uuid4()
