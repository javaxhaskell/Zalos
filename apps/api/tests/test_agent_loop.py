"""End-to-end tests for the bounded :class:`AgentLoop` (BP5a).

These tests are the load-bearing acceptance criterion for BP5a: prove
the loop dispatches a scripted tool sequence through the registry with
the right events, the right idempotency behaviour, the right validation
re-prompt semantics, and the right termination guarantees.

The loop is exercised against a :class:`FakeModelClient` so the tests
are deterministic — every model response is pre-scripted. The real
Anthropic client lands in BP5d and gets a separate live-only test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import (
    FakeModelClient,
    ModelResponse,
    TextBlock,
    ToolUseBlock,
)
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ApprovalStatus,
    ErrorCode,
    EventKind,
    RiskLevel,
    StrictModel,
    ToolDefinition,
    ToolPhase,
    Workflow,
)
from agentforge.tools import (
    RegisteredTool,
    ToolContext,
    ToolRegistry,
    build_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def loop_db(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'loop.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def loop_setup(
    tmp_path: Path, loop_db: Any
) -> Callable[[list[ModelResponse], ToolRegistry | None], tuple]:
    """Build everything the loop needs from a scripted model + registry.

    Returns a callable so each test can stage its own script + registry
    customisations (e.g., register a synthetic write tool to exercise
    the approval gate).
    """

    def _setup(
        script: list[ModelResponse],
        registry: ToolRegistry | None = None,
    ) -> tuple[AgentLoop, UUID, FakeModelClient, WorkspaceManager, EventLog]:
        wm = WorkspaceManager(root=tmp_path / "workspaces")
        sid = uuid4()
        wm.allocate(sid, Workflow.AUTHOR)
        event_log = EventLog(wm)
        idem = IdempotencyStore(db=loop_db)
        client = FakeModelClient(script=script)
        reg = registry or build_registry()
        loop = AgentLoop(
            registry=reg,
            model_client=client,
            idempotency_store=idem,
            event_log=event_log,
            workspace_manager=wm,
            settings=get_settings(),
        )
        return loop, sid, client, wm, event_log

    return _setup


def _resp(
    *,
    content: list,
    stop_reason: str = "tool_use",
    call_id: str = "msg",
) -> ModelResponse:
    return ModelResponse(id=call_id, content=content, stop_reason=stop_reason)


def _budgets(max_steps: int = 5, max_tokens: int = 100_000) -> LoopBudgets:
    return LoopBudgets(
        max_steps=max_steps, max_tokens=max_tokens, max_wall_seconds=60
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_loop_dispatches_scripted_tool_then_terminates(loop_setup) -> None:
    # Turn 1: model calls a read tool; Turn 2: model says done.
    script = [
        _resp(
            content=[
                TextBlock(text="I'll inspect the workspace."),
                ToolUseBlock(
                    id="tu_1",
                    name="list_workspace",
                    input={},
                ),
            ],
            stop_reason="tool_use",
            call_id="msg_1",
        ),
        _resp(
            content=[TextBlock(text="Workspace inspected; that's all for now.")],
            stop_reason="end_turn",
            call_id="msg_2",
        ),
    ]
    loop, sid, client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="be helpful",
        initial_messages=[],
        budgets=_budgets(),
    )

    assert outcome.terminated is True
    assert outcome.paused is False
    assert outcome.steps_taken == 2
    assert client.call_count == 2

    # Events chain: model_called, tool_invoked, tool_observed; then a
    # second model_called for the terminating turn.
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert kinds.count(EventKind.MODEL_CALLED) == 2
    assert kinds.count(EventKind.TOOL_INVOKED) == 1
    assert kinds.count(EventKind.TOOL_OBSERVED) == 1

    valid, msg = event_log.verify_chain(sid)
    assert valid, f"event chain broken: {msg}"


async def test_loop_pauses_user_after_ask_user(loop_setup) -> None:
    script = [
        _resp(
            content=[
                ToolUseBlock(
                    id="tu_question",
                    name="ask_user",
                    input={"question": "Which column contains the merchant name?"},
                ),
            ],
            stop_reason="tool_use",
        )
    ]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )

    assert outcome.terminated is False
    assert outcome.paused is True
    assert outcome.pause_reason == "user"

    questions = [
        evt for evt in event_log.read_all(sid) if evt.kind == EventKind.QUESTION_ASKED
    ]
    assert len(questions) == 1
    assert questions[0].payload["question"] == "Which column contains the merchant name?"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_loop_returns_cache_hit_on_repeated_same_step_call(
    loop_setup,
) -> None:
    # Two consecutive turns that try the SAME call at the SAME step would
    # never happen with a real model — the loop increments step between
    # turns. To exercise the cache, the second tool_use uses the same
    # tool name with the same args within ONE turn (two tool_use blocks
    # in one response → both at step 0).
    script = [
        _resp(
            content=[
                ToolUseBlock(
                    id="tu_1",
                    name="list_workspace",
                    input={},
                ),
                ToolUseBlock(
                    id="tu_2",
                    name="list_workspace",
                    input={},
                ),
            ],
            stop_reason="tool_use",
        ),
        _resp(
            content=[TextBlock(text="done.")],
            stop_reason="end_turn",
        ),
    ]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )

    kinds = [e.kind for e in event_log.read_all(sid)]
    assert kinds.count(EventKind.TOOL_INVOKED) == 1
    assert kinds.count(EventKind.TOOL_OBSERVED) == 2
    assert outcome.terminated is True


# ---------------------------------------------------------------------------
# Validation re-prompt
# ---------------------------------------------------------------------------


async def test_loop_surfaces_validation_failure_as_observation(loop_setup) -> None:
    # Model emits list_workspace with an unexpected argument
    # → validation fails. Next turn provides valid args. Loop completes.
    script = [
        _resp(
            content=[
                ToolUseBlock(id="tu_bad", name="list_workspace", input={"unexpected": True}),
            ],
            stop_reason="tool_use",
        ),
        _resp(
            content=[
                ToolUseBlock(
                    id="tu_good",
                    name="list_workspace",
                    input={},
                ),
            ],
            stop_reason="tool_use",
        ),
        _resp(content=[TextBlock(text="done")], stop_reason="end_turn"),
    ]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )

    assert outcome.terminated is True
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert kinds.count(EventKind.TOOL_INVOKED) == 1
    assert kinds.count(EventKind.TOOL_OBSERVED) == 2


async def test_loop_terminates_after_repeated_validation_failures(
    loop_setup,
) -> None:
    """Three consecutive validation failures → terminate."""
    bad_resp = _resp(
        content=[
            ToolUseBlock(id="tu_bad", name="list_workspace", input={"unexpected": True}),
        ],
        stop_reason="tool_use",
    )
    script = [bad_resp, bad_resp, bad_resp, bad_resp]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )
    assert outcome.terminated is True
    assert outcome.terminal_error_code == ErrorCode.VALIDATION_LOOP_EXHAUSTED
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.WORKFLOW_FAILED in kinds


async def test_loop_advisory_mode_downgrades_validation_loop_exhausted(
    loop_setup,
) -> None:
    """Advisory LLM run: terminal validation-loop exhaustion is recorded as
    ``decision_input{kind=llm_advisory_incomplete}`` rather than
    ``workflow_failed``."""
    bad_resp = _resp(
        content=[
            ToolUseBlock(id="tu_bad", name="list_workspace", input={"unexpected": True}),
        ],
        stop_reason="tool_use",
    )
    script = [bad_resp, bad_resp, bad_resp, bad_resp]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
        advisory_mode=True,
    )
    assert outcome.terminated is True
    assert outcome.terminal_error_code == ErrorCode.VALIDATION_LOOP_EXHAUSTED
    events = event_log.read_all(sid)
    kinds = [e.kind for e in events]
    # No workflow_failed on advisory runs.
    assert EventKind.WORKFLOW_FAILED not in kinds
    # Exactly one llm_advisory_incomplete decision_input.
    advisory = [
        e
        for e in events
        if e.kind == EventKind.DECISION_INPUT
        and e.payload.get("kind") == "llm_advisory_incomplete"
    ]
    assert len(advisory) == 1
    assert advisory[0].payload.get("reason") == "validation_loop_exhausted"


async def test_loop_advisory_mode_downgrades_budget_exhausted(
    loop_setup,
) -> None:
    """Advisory + step budget exhausted → decision_input, not budget_exhausted."""
    good_resp = _resp(
        content=[
            ToolUseBlock(
                id="tu_good",
                name="list_workspace",
                input={},
            ),
        ],
        stop_reason="tool_use",
    )
    # Script enough to exceed a step cap of 1.
    script = [good_resp, good_resp, good_resp]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=1, max_tokens=200_000, max_wall_seconds=60),
        advisory_mode=True,
    )
    assert outcome.terminated is True
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.WORKFLOW_FAILED not in kinds
    assert EventKind.BUDGET_EXHAUSTED not in kinds
    advisory = [
        e
        for e in event_log.read_all(sid)
        if e.kind == EventKind.DECISION_INPUT
        and e.payload.get("kind") == "llm_advisory_incomplete"
    ]
    assert len(advisory) == 1


# ---------------------------------------------------------------------------
# Tool not registered
# ---------------------------------------------------------------------------


async def test_loop_surfaces_unregistered_tool_then_continues(loop_setup) -> None:
    script = [
        _resp(
            content=[
                ToolUseBlock(id="tu_x", name="nonexistent_tool", input={}),
            ],
            stop_reason="tool_use",
        ),
        _resp(content=[TextBlock(text="ok, giving up")], stop_reason="end_turn"),
    ]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )
    assert outcome.terminated is True
    # No tool actually dispatched → no TOOL_INVOKED event.
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.TOOL_INVOKED not in kinds


# ---------------------------------------------------------------------------
# Budgets (INV-12)
# ---------------------------------------------------------------------------


async def test_loop_terminates_when_step_budget_exhausted(loop_setup) -> None:
    # Model returns a tool_use forever; budget cap fires before script.
    forever = _resp(
        content=[
            ToolUseBlock(
                id="tu_z",
                name="list_workspace",
                input={"path": "."},
            ),
        ],
        stop_reason="tool_use",
    )
    loop, sid, _client, _wm, event_log = loop_setup([forever] * 10, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(max_steps=2),
    )
    assert outcome.terminated is True
    assert outcome.terminal_error_code == ErrorCode.BUDGET_EXHAUSTED_STEPS
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.BUDGET_EXHAUSTED in kinds


# ---------------------------------------------------------------------------
# Approval gate (INV-3)
# ---------------------------------------------------------------------------


class _PingInput(StrictModel):
    msg: str = "ping"


class _PingOutput(StrictModel):
    echoed: str


async def _ping_handler(args: _PingInput, ctx: ToolContext) -> _PingOutput:
    return _PingOutput(echoed=args.msg)


def _registry_with_approval_tool() -> ToolRegistry:
    """A registry containing a synthetic write tool that needs approval."""
    reg = build_registry()
    reg.register(
        RegisteredTool(
            definition=ToolDefinition(
                name="needs_approval_tool",
                description="synthetic write tool that requires approval",
                input_schema_name="_PingInput",
                output_schema_name="_PingOutput",
                risk_level=RiskLevel.LOW_WRITE,
                requires_approval=True,
                idempotent=True,
                phases=[ToolPhase.AUTHOR_INFO],
                authorize_callable="agentforge.tools.authz.allow_authenticated_users",
            ),
            input_schema=_PingInput,
            output_schema=_PingOutput,
            handler=_ping_handler,
        )
    )
    return reg


async def test_loop_pauses_on_unapproved_write_tool(loop_setup) -> None:
    script = [
        _resp(
            content=[
                ToolUseBlock(id="tu_a", name="needs_approval_tool", input={}),
            ],
            stop_reason="tool_use",
        ),
    ]
    reg = _registry_with_approval_tool()
    loop, sid, _client, _wm, event_log = loop_setup(script, reg)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )
    assert outcome.paused is True
    assert outcome.pause_reason == "approval"
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.APPROVAL_REQUESTED in kinds
    assert EventKind.TOOL_INVOKED not in kinds  # paused BEFORE dispatch


async def test_loop_dispatches_write_tool_after_approval_granted(loop_setup) -> None:
    script = [
        _resp(
            content=[
                ToolUseBlock(id="tu_a", name="needs_approval_tool", input={}),
            ],
            stop_reason="tool_use",
        ),
        _resp(content=[TextBlock(text="done")], stop_reason="end_turn"),
    ]
    reg = _registry_with_approval_tool()
    loop, sid, _client, _wm, event_log = loop_setup(script, reg)

    # Pre-record APPROVAL_GRANTED for step 0.
    from agentforge.schemas import ActorType

    event_log.append(
        session_id=sid,
        kind=EventKind.APPROVAL_GRANTED,
        actor_type=ActorType.USER,
        payload={
            "request_id": "synthetic-test",
            "step": 0,
            "status": ApprovalStatus.GRANTED.value,
        },
        step=0,
    )

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_INFO,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )
    assert outcome.terminated is True
    assert outcome.paused is False
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.TOOL_INVOKED in kinds
    assert EventKind.TOOL_OBSERVED in kinds


# ---------------------------------------------------------------------------
# Phase filtering
# ---------------------------------------------------------------------------


async def test_loop_rejects_tool_not_in_current_phase(loop_setup) -> None:
    """inspect_csv_schema is INFO-phase only; calling from BUILD must fail."""
    script = [
        _resp(
            content=[
                ToolUseBlock(
                    id="tu_x",
                    name="inspect_csv_schema",
                    input={"path": "uploads/a.csv", "file_id": str(uuid4())},
                ),
            ],
            stop_reason="tool_use",
        ),
        _resp(content=[TextBlock(text="ok")], stop_reason="end_turn"),
    ]
    loop, sid, _client, _wm, event_log = loop_setup(script, None)

    outcome = await loop.run(
        session_id=sid,
        phase=ToolPhase.AUTHOR_BUILD,
        system_prompt="s",
        initial_messages=[],
        budgets=_budgets(),
    )
    assert outcome.terminated is True
    # Tool not invoked; surfaced as not_registered observation.
    last_obs = outcome.last_observations
    assert last_obs == []  # because final turn was end_turn (no tool_uses)
    kinds = [e.kind for e in event_log.read_all(sid)]
    assert EventKind.TOOL_INVOKED not in kinds
