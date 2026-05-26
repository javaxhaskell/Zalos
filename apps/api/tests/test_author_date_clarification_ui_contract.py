"""Frontend contract checks for date clarification UI (free-text, no fixed buttons)."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PANEL = _REPO_ROOT / "apps/web/src/components/user-question-panel.tsx"
_AUTHOR_PAGE = _REPO_ROOT / "apps/web/app/author/[sid]/page.tsx"


def test_user_question_panel_uses_free_text_only() -> None:
    source = _PANEL.read_text(encoding="utf-8")
    assert 'type="radio"' not in source
    assert "<select" not in source
    assert "textarea" in source
    assert "USER_QUESTION_PANEL_COPY" in source
    assert "copy.submitButton" in source
    assert "sampleValues" in source
    assert "copy.dateQuestionText" in source
    assert "humaniseAgentQuestion(questionText)" not in source or "isDateClarification" in source


def test_author_page_passes_clarification_samples_to_panel() -> None:
    source = _AUTHOR_PAGE.read_text(encoding="utf-8")
    assert "sampleValues={pendingQuestion.sampleValues}" in source
    assert "affectedColumns={pendingQuestion.affectedColumns}" in source
    assert "clarificationKind={pendingQuestion.clarificationKind}" in source
    assert "sample_values" in source
