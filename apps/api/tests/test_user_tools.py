"""Tests for user-interaction tools."""

from __future__ import annotations

from agentforge.schemas import EventKind, QuestionStyle
from agentforge.tools.user_tools import AskUserInput, ask_user_handler


async def test_ask_user_records_question_and_returns_pause_marker(tool_ctx) -> None:
    result = await ask_user_handler(
        AskUserInput(
            question="Which column contains the merchant name?",
            style=QuestionStyle.FREE_TEXT,
        ),
        tool_ctx,
    )

    assert result.status == "paused_user"
    assert result.question == "Which column contains the merchant name?"

    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    questions = [evt for evt in events if evt.kind == EventKind.QUESTION_ASKED]
    assert len(questions) == 1
    assert questions[0].payload["question"] == result.question
    assert questions[0].payload["plain_english_question"] == result.question
    assert questions[0].payload["style"] == "free_text"
