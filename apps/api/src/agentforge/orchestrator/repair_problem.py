"""Primary problem resolution and user-report / evidence alignment for Repair.

Precedence (load-bearing for audit reports and the completed UI):

  1. Non-empty wizard ``user_message`` → ``user_input``
  2. Else embedded ``problem_report.md`` after ZIP upload → ``uploaded_problem_report``
  3. Else embedded report on a bundled fixture → ``built_in_sample_problem_report``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID

from agentforge.orchestrator.repair_proposal import RepairProposal
from agentforge.persistence.event_log import EventLog
from agentforge.schemas import EventKind

PrimaryProblemSource = Literal[
    "user_input",
    "uploaded_problem_report",
    "built_in_sample_problem_report",
]

_STOPWORDS = frozenset(
    {
        "agent",
        "being",
        "could",
        "describe",
        "from",
        "into",
        "invoice",
        "invoices",
        "issue",
        "placed",
        "please",
        "problem",
        "report",
        "repair",
        "someone",
        "that",
        "their",
        "this",
        "want",
        "when",
        "with",
        "wrong",
    }
)


@dataclass(frozen=True)
class ResolvedPrimaryProblem:
    """The problem statement the repair pipeline treats as authoritative."""

    text: str
    source: PrimaryProblemSource
    uploaded_problem_report: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProblemAlignment:
    """Whether the primary problem matches reproduced failing evidence."""

    aligned: bool
    discovered_issue_summary: str = ""
    note: str = ""


def read_embedded_problem_report(working_dir: Path) -> str | None:
    """Return ``problem_report.md`` text from the agent tree, if present."""
    for candidate in (
        working_dir / "problem_report.md",
        working_dir / "data" / "problem_report.md",
    ):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    return None


def resolve_primary_problem(
    *,
    session_id: UUID,
    working_dir: Path,
    event_log: EventLog,
) -> ResolvedPrimaryProblem:
    """Apply problem-report precedence and collect supporting context notes."""
    user_text = _read_user_problem(event_log, session_id)
    embedded = read_embedded_problem_report(working_dir)
    load_kind = _agent_load_kind(event_log, session_id)

    notes: list[str] = []

    if user_text:
        source: PrimaryProblemSource = "user_input"
        primary = user_text
        if embedded and not _texts_equivalent(primary, embedded):
            notes.append(
                "User typed a problem in the wizard; embedded problem_report.md "
                "was kept as supporting context only and did not override user input."
            )
        return ResolvedPrimaryProblem(
            text=primary,
            source=source,
            uploaded_problem_report=embedded if embedded and not _texts_equivalent(primary, embedded) else None,
            notes=notes,
        )

    if embedded:
        if load_kind == "fixture_loaded":
            source = "built_in_sample_problem_report"
        else:
            source = "uploaded_problem_report"
        return ResolvedPrimaryProblem(
            text=embedded,
            source=source,
            uploaded_problem_report=None,
            notes=notes,
        )

    return ResolvedPrimaryProblem(
        text="(no problem report provided; see uploads/ and working/)",
        source="uploaded_problem_report",
        notes=["No user problem or embedded problem_report.md was available."],
    )


def assess_problem_evidence_alignment(
    primary: ResolvedPrimaryProblem,
    *,
    failing_test_names: list[str],
    pytest_excerpt: str,
    proposal: RepairProposal | None = None,
) -> ProblemAlignment:
    """Return whether the primary problem matches reproduced failing evidence."""
    if primary.source != "user_input":
        return ProblemAlignment(aligned=True)

    discovered = _summarise_discovered_issue(
        failing_test_names=failing_test_names,
        pytest_excerpt=pytest_excerpt,
        proposal=proposal,
    )
    if not failing_test_names:
        return ProblemAlignment(
            aligned=False,
            discovered_issue_summary=discovered,
            note="User-reported problem could not be checked against failing tests because none failed.",
        )

    problem_themes = _themes_from_problem(primary.text)
    failing_themes = _themes_from_failing_tests(failing_test_names)

    if not problem_themes:
        return ProblemAlignment(aligned=True, discovered_issue_summary=discovered)

    if problem_themes & failing_themes:
        return ProblemAlignment(aligned=True, discovered_issue_summary=discovered)

    # Strong mismatch: user emphasises a theme no failing test reproduces.
    if problem_themes and failing_themes:
        return ProblemAlignment(
            aligned=False,
            discovered_issue_summary=discovered,
            note=(
                "User-reported problem does not align with the reproduced failing "
                f"tests ({', '.join(failing_test_names[:3])}). "
                f"Observed failure instead: {discovered}"
            ),
        )

    return ProblemAlignment(aligned=True, discovered_issue_summary=discovered)


def _read_user_problem(event_log: EventLog, session_id: UUID) -> str | None:
    for evt in reversed(event_log.read_all(session_id)):
        if evt.kind != EventKind.DECISION_INPUT:
            continue
        if evt.payload.get("kind") != "repair_user_problem":
            continue
        text = evt.payload.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _agent_load_kind(event_log: EventLog, session_id: UUID) -> str | None:
    for evt in event_log.read_all(session_id):
        if evt.kind != EventKind.DECISION_INPUT:
            continue
        kind = evt.payload.get("kind")
        if kind in {"fixture_loaded", "agent_zip_uploaded"}:
            return str(kind)
    return None


def _texts_equivalent(a: str, b: str) -> bool:
    return _normalise_problem_text(a) == _normalise_problem_text(b)


def _normalise_problem_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _themes_from_problem(text: str) -> set[str]:
    lowered = text.lower()
    themes: set[str] = set()
    if any(token in lowered for token in ("paid invoice", "paid invoices", "mark paid")):
        themes.add("paid")
    if any(
        token in lowered
        for token in ("31-day", "31 day", "1-30", "31-60", "boundary", "exactly 31")
    ):
        themes.add("boundary")
    if any(token in lowered for token in ("parse", "format", "date parsing", "mm-dd")):
        themes.add("date_format")
    if "april" in lowered and "bucket" in lowered:
        themes.add("date_format")
    return themes


def _themes_from_failing_tests(names: list[str]) -> set[str]:
    themes: set[str] = set()
    for name in names:
        lowered = name.lower()
        if "paid" in lowered:
            themes.add("paid")
        if "boundary" in lowered or "31" in lowered:
            themes.add("boundary")
        if any(token in lowered for token in ("april", "format", "parse", "date")):
            themes.add("date_format")
    return themes


def _summarise_discovered_issue(
    *,
    failing_test_names: list[str],
    pytest_excerpt: str,
    proposal: RepairProposal | None,
) -> str:
    if proposal is not None and proposal.root_cause.strip():
        return proposal.root_cause.strip()
    if failing_test_names:
        return f"Failing tests: {', '.join(failing_test_names[:3])}"
    excerpt = pytest_excerpt.strip().splitlines()
    if excerpt:
        return excerpt[0][:200]
    return "Unknown failure pattern"


__all__ = [
    "PrimaryProblemSource",
    "ProblemAlignment",
    "ResolvedPrimaryProblem",
    "assess_problem_evidence_alignment",
    "read_embedded_problem_report",
    "resolve_primary_problem",
]
