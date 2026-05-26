"""LLM-backed Author custom build policy tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.config import Settings, get_settings
from agentforge.models import FakeModelClient, ModelResponse, TextBlock
from agentforge.orchestrator.author_custom_build import (
    UploadedDataFile,
    execute_custom_workflow_pipeline,
)
from agentforge.orchestrator.author_llm_authoring import AI_AUTHORED_WORKFLOW_BUILD_VIA
from tests.author_model_fixtures import model_ack_only_responses
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ErrorCode, EventKind, SessionStatus, Workflow

_EXPENSE_PROMPT = (
    "Review this employee expense export for policy exceptions. Flag rows that "
    "need investigation and produce a clean output file with validation evidence."
)


def _model_stage_script(count: int = 2) -> list[ModelResponse]:
    return model_ack_only_responses(count)


def _write_expense_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "expense_id,employee_id,expense_date,category,amount,description\n"
        "EXP-1,EMP-01,2026-01-10,Travel,120.00,Client taxi\n",
        encoding="utf-8",
    )


def test_unknown_workflow_requires_model_call(workspaces_root: Path) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "expense_exceptions.csv"
    _write_expense_csv(upload)
    event_log = EventLog(wm)
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="expense_exception_review",
            user_description=_EXPENSE_PROMPT,
            settings=get_settings(),
            event_log=event_log,
            workspace_manager=wm,
            step=1,
            model_client=None,
        )
    )
    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING


def test_unknown_workflow_records_model_call_but_does_not_fake_complete(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "expense_exceptions.csv"
    _write_expense_csv(upload)
    event_log = EventLog(wm)
    model = FakeModelClient(script=_model_stage_script())
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="expense_exception_review",
            user_description=_EXPENSE_PROMPT,
            settings=get_settings(),
            event_log=event_log,
            workspace_manager=wm,
            step=1,
            model_client=model,
        )
    )
    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    events = event_log.read_all(sid)
    assert sum(1 for event in events if event.kind == EventKind.MODEL_CALLED) >= 1


def test_blind_eval_mode_rejects_zero_token_custom_completion(workspaces_root: Path) -> None:
    from agentforge.orchestrator.author_blind_eval import BlindEvalIntegrityError
    from agentforge.orchestrator.author_blind_eval import assert_blind_eval_completion_integrity

    settings = Settings(author_blind_eval_mode=True)
    with pytest.raises(BlindEvalIntegrityError):
        assert_blind_eval_completion_integrity(
            settings=settings,
            events=[],
            workflow_type="expense_exception_review",
            build_mode="llm_custom",
            completion_via=AI_AUTHORED_WORKFLOW_BUILD_VIA,
            model_calls=0,
        )
