"""User-interaction tools.

``ask_user`` is the model's only path to pausing a workflow for a
clarifying question. The handler records a canonical ``QUESTION_ASKED``
event; the agent loop then returns ``paused_user`` to the orchestrator.
The frontend answers through ``POST /sessions/{id}/answer``.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from agentforge.schemas import (
    ActorType,
    EventKind,
    PendingQuestion,
    QuestionStyle,
    RiskLevel,
    StrictModel,
    ToolDefinition,
    ToolPhase,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

_AUTHORIZE = "agentforge.tools.authz.allow_authenticated_users"


class AskUserInput(StrictModel):
    """Clarifying question to surface to the finance user."""

    question: str = Field(min_length=1, max_length=2_000)
    style: QuestionStyle = QuestionStyle.FREE_TEXT
    options: list[str] = Field(default_factory=list, max_length=12)


class AskUserOutput(StrictModel):
    """Recorded question event; the loop pauses after this returns."""

    question_id: UUID
    question: str
    style: QuestionStyle
    options: list[str] = Field(default_factory=list)
    status: Literal["paused_user"] = "paused_user"


async def ask_user_handler(args: AskUserInput, ctx: ToolContext) -> AskUserOutput:
    """Record a typed question and return the event id to the loop."""

    question = PendingQuestion(
        session_id=ctx.session_id,
        text=args.question,
        style=args.style,
        options=args.options,
    )
    event = ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.QUESTION_ASKED,
        actor_type=ActorType.MODEL,
        payload={
            "question_id": str(question.id),
            "question": question.text,
            "plain_english_question": question.text,
            "style": question.style.value,
            "options": question.options,
        },
        step=ctx.step,
    )
    return AskUserOutput(
        question_id=event.id,
        question=question.text,
        style=question.style,
        options=question.options,
    )


ASK_USER_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="ask_user",
        description=(
            "Ask the finance user a short clarifying question and pause the "
            "session until they answer."
        ),
        input_schema_name="AskUserInput",
        output_schema_name="AskUserOutput",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=[
            ToolPhase.AUTHOR_INFO,
            ToolPhase.AUTHOR_BUILD,
            ToolPhase.REPAIR_INFO,
            ToolPhase.REPAIR_FIX,
        ],
        authorize_callable=_AUTHORIZE,
    ),
    input_schema=AskUserInput,
    output_schema=AskUserOutput,
    handler=ask_user_handler,
)


__all__ = [
    "ASK_USER_TOOL",
    "AskUserInput",
    "AskUserOutput",
    "ask_user_handler",
]
