"""End-to-end Author flow tests for the LLM-first path."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import FakeModelClient
from agentforge.orchestrator import AuthorFlow
from agentforge.orchestrator.author_llm_authoring import AI_AUTHORED_WORKFLOW_BUILD_VIA
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, ErrorCode, EventKind, SessionStatus, Workflow
from agentforge.tools import build_registry
from tests.author_model_fixtures import model_ack_only_responses, model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"


def _bank_model_responses():
    return model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )


@pytest.fixture()
def e2e_db(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'e2e.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _make_flow(workspaces_root: Path, e2e_db, client: FakeModelClient):
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    shutil.copy(_BANK_DIR / "data" / "sample_input.csv", workspace / "uploads" / "sample_input.csv")
    event_log = EventLog(wm)
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={
            "kind": "author_user_workflow",
            "text": "Categorise bank transactions from the uploaded CSV.",
        },
        step=0,
    )
    event_log.append(
        session_id=sid,
        kind=EventKind.FILE_UPLOADED,
        actor_type=ActorType.USER,
        payload={
            "filename": "sample_input.csv",
            "relative_path": "uploads/sample_input.csv",
            "size_bytes": (workspace / "uploads" / "sample_input.csv").stat().st_size,
        },
        step=0,
    )
    settings = get_settings()
    loop = AgentLoop(
        registry=build_registry(),
        model_client=client,
        idempotency_store=IdempotencyStore(db=e2e_db),
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    flow = AuthorFlow(
        agent_loop=loop,
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    return wm, event_log, sid, workspace, flow


@pytest.mark.asyncio
async def test_author_flow_completes_bank_with_model_authored_artifacts(
    workspaces_root: Path,
    e2e_db,
) -> None:
    wm, event_log, sid, workspace, flow = _make_flow(
        workspaces_root,
        e2e_db,
        FakeModelClient(script=_bank_model_responses()),
    )

    outcome = await flow.run(
        session_id=sid,
        system_prompt="You are an agent author for finance workflows.",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=20, max_tokens=200_000, max_wall_seconds=120),
    )

    assert outcome.terminal_status == SessionStatus.COMPLETED
    assert outcome.terminal_error_code is None
    assert outcome.build_outcome is None
    assert (workspace / "generated" / "model_contract_plan.json").is_file()
    assert (workspace / "generated" / "model_contract_review.json").is_file()
    assert (workspace / "generated" / "model_code_plan.json").is_file()
    assert (workspace / "generated" / "agent.py").is_file()
    assert (workspace / "generated" / "tests" / "test_agent.py").is_file()
    assert (workspace / "outputs" / "output.csv").is_file()
    assert (workspace / "reports" / "validation_report.md").is_file()
    assert (workspace / "reports" / "model_authoring_summary.md").is_file()
    assert (workspace / "SESSION_README.md").is_file()
    assert (workspace / "archive.zip").is_file()

    input_rows = _read_csv(workspace / "uploads" / "sample_input.csv")
    output_rows = _read_csv(workspace / "outputs" / "output.csv")
    assert len(output_rows) == len(input_rows)
    assert {"category", "rule_used", "confidence"} <= set(output_rows[0])

    events = event_log.read_all(sid)
    assert not any(event.kind == EventKind.TEMPLATE_SEEDED for event in events)
    assert not any(event.kind == EventKind.WORKFLOW_FAILED for event in events)
    assert sum(1 for event in events if event.kind == EventKind.MODEL_CALLED) >= 4
    validation = next(event for event in events if event.kind == EventKind.VALIDATION_RUN)
    assert validation.payload["overall_passed"] is True
    completed = next(event for event in events if event.kind == EventKind.WORKFLOW_COMPLETED)
    assert completed.payload.get("via") == AI_AUTHORED_WORKFLOW_BUILD_VIA

    manifest = wm.read_manifest(sid)
    assert manifest.status == SessionStatus.COMPLETED
    completion = manifest.completion.model_dump(mode="json") if manifest.completion else {}
    assert completion.get("model_call_count", 0) >= 4
    assert completion.get("model_contributed") is True
    assert "generated/agent.py" in completion.get("model_contributed_files", [])

    valid, msg = event_log.verify_chain(sid)
    assert valid, f"event chain broken: {msg}"


@pytest.mark.asyncio
async def test_author_flow_bad_model_fails_without_artifacts(
    workspaces_root: Path,
    e2e_db,
) -> None:
    _wm, event_log, sid, workspace, flow = _make_flow(
        workspaces_root,
        e2e_db,
        FakeModelClient(script=model_ack_only_responses(1)),
    )

    outcome = await flow.run(
        session_id=sid,
        system_prompt="You are an agent author for finance workflows.",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=50_000, max_wall_seconds=60),
    )

    assert outcome.terminal_status == SessionStatus.FAILED_OTHER
    assert outcome.terminal_error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    assert not (workspace / "outputs" / "output.csv").exists()
    events = event_log.read_all(sid)
    assert any(event.kind == EventKind.MODEL_CALLED for event in events)
    assert not any(event.kind == EventKind.WORKFLOW_COMPLETED for event in events)
