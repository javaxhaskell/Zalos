"""Author intent/schema alignment gate tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import FakeModelClient, ModelResponse, ToolUseBlock
from agentforge.orchestrator import AuthorFlow
from agentforge.orchestrator.author_intent import assess_intent_schema_alignment
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind, SessionStatus, Workflow
from agentforge.orchestrator.author_llm_authoring import AI_AUTHORED_WORKFLOW_BUILD_VIA
from agentforge.tools import build_registry
from tests.author_model_fixtures import model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"

_BANK_DESCRIPTION = (
    "Categorise each bank transaction into Income, Office Expense, Travel, "
    "Subscriptions, Refund, or Uncategorised using a rule cascade."
)
_INVOICE_AGING_DESCRIPTION = (
    "Create an invoice aging agent that reads invoices, computes days overdue "
    "from due_date, assigns aging buckets, and flags high-risk overdue invoices."
)


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


def _scripted_info_asks_counterparty_first() -> list[ModelResponse]:
    return [
        ModelResponse(
            id="msg_info_ask",
            content=[
                ToolUseBlock(
                    id="tu_ask",
                    name="ask_user",
                    input={
                        "question": (
                            "The schema indicates the counterparty column may contain nulls. "
                            "Are these missing values in error or intended to represent "
                            "unknown counterparties?"
                        ),
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
    ]


def _scripted_info_only(file_id) -> list[ModelResponse]:
    del file_id
    return model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )


def _record_run_context(
    event_log: EventLog,
    session_id,
    *,
    template_hint: str = "bank_categoriser",
    workflow_text: str | None = None,
) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={"kind": "template_hint", "value": template_hint},
        step=0,
    )
    if workflow_text:
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.USER,
            payload={"kind": "author_user_workflow", "text": workflow_text},
            step=0,
        )
    event_log.append(
        session_id=session_id,
        kind=EventKind.FILE_UPLOADED,
        actor_type=ActorType.USER,
        payload={
            "filename": "sample_input.csv",
            "relative_path": "uploads/sample_input.csv",
            "size_bytes": 1234,
        },
        step=0,
    )


async def _run_author_with_description(
    workspaces_root: Path,
    e2e_db,
    workflow_text: str | None,
    *,
    info_script=None,
):
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    settings = get_settings()
    shutil.copy(
        _BANK_DIR / "data" / "sample_input.csv",
        workspace / "uploads" / "sample_input.csv",
    )
    event_log = EventLog(wm)
    _record_run_context(
        event_log,
        sid,
        workflow_text=workflow_text,
    )
    file_id = uuid4()
    script = info_script if info_script is not None else _scripted_info_only(file_id)
    loop = AgentLoop(
        registry=build_registry(),
        model_client=FakeModelClient(script=script),
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
    return wm, event_log, sid, workspace, outcome


def test_assess_allows_bank_prompt_with_bank_schema() -> None:
    cols = ["txn_id", "date", "amount", "description", "counterparty", "account"]
    result = assess_intent_schema_alignment(
        user_description=_BANK_DESCRIPTION,
        template_name="bank_categoriser",
        csv_columns=cols,
    )
    assert result.aligned is True


def test_assess_blocks_invoice_prompt_with_bank_schema() -> None:
    cols = ["txn_id", "date", "amount", "description", "counterparty", "account"]
    result = assess_intent_schema_alignment(
        user_description=_INVOICE_AGING_DESCRIPTION,
        template_name="bank_categoriser",
        csv_columns=cols,
    )
    assert result.aligned is False
    assert result.requested_workflow == "invoice_aging"
    assert result.detected_file_type == "bank_transactions"
    assert "invoice_id" in result.missing_columns


@pytest.mark.asyncio
async def test_bank_categorisation_prompt_still_completes(
    workspaces_root: Path,
    e2e_db,
) -> None:
    wm, event_log, sid, workspace, outcome = await _run_author_with_description(
        workspaces_root, e2e_db, _BANK_DESCRIPTION
    )
    assert outcome.terminal_status == SessionStatus.COMPLETED
    assert (workspace / "outputs" / "output.csv").is_file()
    events = event_log.read_all(sid)
    assert not any(e.kind == EventKind.WORKFLOW_FAILED for e in events)
    completed = next(e for e in events if e.kind == EventKind.WORKFLOW_COMPLETED)
    assert completed.payload.get("via") == AI_AUTHORED_WORKFLOW_BUILD_VIA
    assert sum(1 for event in events if event.kind == EventKind.MODEL_CALLED) > 0


@pytest.mark.asyncio
async def test_invoice_aging_prompt_with_bank_csv_does_not_complete(
    workspaces_root: Path,
    e2e_db,
) -> None:
    wm, event_log, sid, workspace, outcome = await _run_author_with_description(
        workspaces_root, e2e_db, _INVOICE_AGING_DESCRIPTION
    )
    assert outcome.terminal_status == SessionStatus.FAILED_OTHER
    assert outcome.terminal_error_code is not None
    assert outcome.terminal_error_code.value == "author_intent_schema_mismatch"
    assert not (workspace / "outputs" / "output.csv").exists()
    assert not (workspace / "reports" / "validation_report.md").exists()

    events = event_log.read_all(sid)
    mismatch = next(
        e
        for e in events
        if e.kind == EventKind.DECISION_INPUT
        and e.payload.get("kind") == "author_intent_schema_mismatch"
    )
    assert mismatch.payload["detected_file_type"] == "bank_transactions"
    assert mismatch.payload["requested_workflow"] == "invoice_aging"
    assert "invoice_id" in mismatch.payload["missing_columns"]
    assert "bank transactions" in mismatch.payload["explanation"].lower()
    assert not any(e.kind == EventKind.WORKFLOW_COMPLETED for e in events)
    assert not any(e.kind == EventKind.VALIDATION_RUN for e in events)
    assert not any(e.kind == EventKind.MODEL_CALLED for e in events)
    assert not any(e.kind == EventKind.QUESTION_ASKED for e in events)


@pytest.mark.asyncio
async def test_invoice_aging_mismatch_skips_info_schema_questions(
    workspaces_root: Path,
    e2e_db,
) -> None:
    """INFO loop must not run (and ask counterparty nulls) on obvious mismatch."""
    _, event_log, sid, workspace, outcome = await _run_author_with_description(
        workspaces_root,
        e2e_db,
        _INVOICE_AGING_DESCRIPTION,
        info_script=_scripted_info_asks_counterparty_first(),
    )
    assert outcome.terminal_error_code is not None
    assert outcome.terminal_error_code.value == "author_intent_schema_mismatch"
    assert not (workspace / "outputs" / "output.csv").exists()

    events = event_log.read_all(sid)
    assert not any(e.kind == EventKind.QUESTION_ASKED for e in events)
    assert not any(
        e.kind == EventKind.QUESTION_ASKED
        and "counterparty" in str(e.payload.get("plain_english_question", "")).lower()
        for e in events
    )
