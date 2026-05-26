"""Orchestrated tool-action audit events on Author and Repair happy paths."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import FakeModelClient
from agentforge.orchestrator import AuthorFlow, RepairFlow
from agentforge.orchestrator.tool_scope_audit import tool_action_events
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind, SessionStatus, Workflow
from agentforge.tools import build_registry
from tests.author_model_fixtures import model_authoring_responses
from tests.test_author_custom_workflow_gate import (
    _PAYMENT_DIR,
    _PAYMENT_RECON_DESCRIPTION,
    _record_run_context,
    _write_payment_recon_xlsx,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_V2_FIXTURE = _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v2"
_DOCS_TOOL_SCOPE = _REPO_ROOT / "docs" / "TOOL_SCOPE_MODEL.md"


def _payment_model_responses():
    return model_authoring_responses(
        template_root=_PAYMENT_DIR,
        workflow_type="payment_processor_reconciliation",
        contract_overrides={
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["outputs/summary_by_settlement_batch.csv"],
        },
    )


def _tool_names(actions: list[dict]) -> set[str]:
    return {str(a["tool_name"]) for a in actions}


def _statuses_for(actions: list[dict], tool_name: str) -> set[str]:
    return {
        str(a["status"])
        for a in actions
        if a.get("tool_name") == tool_name
    }


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
    import shutil

    working = workspace / "working"
    working.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        fixture,
        working / fixture.name,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".DS_Store", "*.pyc"
        ),
    )


async def _run_author_success(workspaces_root: Path, e2e_db) -> list[dict]:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    settings = get_settings()
    _write_payment_recon_xlsx(
        workspace / "uploads" / "payment_processor_reconciliation_sample.xlsx"
    )
    event_log = EventLog(wm)
    _record_run_context(
        event_log,
        sid,
        template_hint="bank_categoriser",
        workflow_text=_PAYMENT_RECON_DESCRIPTION,
    )
    loop = AgentLoop(
        registry=build_registry(),
        model_client=FakeModelClient(script=_payment_model_responses()),
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
    outcome = await flow.run(
        session_id=sid,
        system_prompt="author",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=120),
    )
    assert outcome.terminal_status == SessionStatus.COMPLETED
    return tool_action_events(event_log.read_all(sid))


async def _run_repair_success(workspaces_root: Path, e2e_db) -> list[dict]:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.REPAIR)
    workspace = wm.get(sid)
    _stage_fixture(workspace, _V2_FIXTURE)
    event_log = EventLog(wm)
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={"kind": "fixture_loaded", "fixture_name": "invoice_aging_v2"},
        step=0,
    )
    settings = get_settings()
    loop = AgentLoop(
        registry=build_registry(),
        model_client=FakeModelClient(script=[]),
        idempotency_store=IdempotencyStore(db=e2e_db),
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    flow = RepairFlow(
        agent_loop=loop,
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    outcome = await flow.run(
        session_id=sid,
        system_prompt="repair",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=6, max_tokens=50_000, max_wall_seconds=120),
    )
    assert outcome.terminal_status == SessionStatus.COMPLETED
    return tool_action_events(event_log.read_all(sid))


def test_docs_describe_two_tool_layers() -> None:
    text = _DOCS_TOOL_SCOPE.read_text(encoding="utf-8")
    assert "AgentLoop" in text or "agent loop" in text.lower()
    assert "orchestrator" in text.lower()
    assert "tool_action_recorded" in text or "tool_action" in text


def test_author_success_emits_orchestrator_tool_actions(
    workspaces_root: Path,
    e2e_db,
) -> None:
    actions = asyncio.run(_run_author_success(workspaces_root, e2e_db))
    names = _tool_names(actions)
    assert "inspect_csv_schema" in names or "inspect_file" in names
    assert "contract_planning" in names
    assert "code_generation" in names
    assert "test_generation" in names
    assert "generated_agent_execution" in names
    assert "generated_pytest" in names
    assert "deterministic_validation" in names
    assert "archive_generation" in names

    for action in actions:
        assert action["dispatch_mode"] == "orchestrator"
        assert action["workflow"] == "author"
        assert action["phase"] == "author.build"
        assert action["approval_policy"] == "auto_approved_backend_validation"

    assert "model" in {a["controlled_by"] for a in actions}
    assert "backend" in {a["controlled_by"] for a in actions}
    assert "generated_code" in {a["controlled_by"] for a in actions}

    assert "completed" in _statuses_for(actions, "archive_generation")


def test_repair_success_emits_orchestrator_tool_actions(
    workspaces_root: Path,
    e2e_db,
) -> None:
    actions = asyncio.run(_run_repair_success(workspaces_root, e2e_db))
    names = _tool_names(actions)
    assert "list_workspace" in names
    assert "inspect_file" in names
    assert "pytest_before_fix" in names
    assert "pytest_after_fix" in names
    assert "repair_proposal" in names
    assert "apply_patch" in names
    assert "generate_repair_report" in names

    for action in actions:
        assert action["dispatch_mode"] == "orchestrator"
        assert action["workflow"] == "repair"
        assert action["phase"] == "repair.fix"

    assert "model" in {a["controlled_by"] for a in actions if a["tool_name"] == "repair_proposal"}
    assert "backend" in {
        a["controlled_by"]
        for a in actions
        if a["tool_name"] in {"apply_patch", "pytest_before_fix"}
    }

    assert "completed" in _statuses_for(actions, "pytest_after_fix")


def test_registry_still_registers_expected_tools() -> None:
    registry = build_registry()
    names = set(registry.all_names())
    assert "inspect_file" in names
    assert "run_pytest" in names
    assert "apply_patch" in names
    assert registry.list_for_phase("author.build")
    assert registry.list_for_phase("repair.fix")
