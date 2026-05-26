"""Deterministic date-format clarification for Author uploads (INV-1, INV-6, INV-8).

Detects ambiguous slash/dash dates in known date columns before model authoring,
pauses for a free-text user answer, parses DMY vs MDY without guessing, and
persists the result for downstream contract/codegen/test prompts.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from agentforge.persistence.event_log import EventLog
from agentforge.schemas import ActorType, EventKind, SessionStatus

DateFormatPreference = Literal["DMY", "MDY"]

_DATE_COLUMN_NAMES = frozenset(
    {
        "invoice_date",
        "due_date",
        "payment_date",
        "expense_date",
        "transaction_date",
        "date",
    }
)
_DATE_3PART = re.compile(r"^\d{1,4}[-/]\d{1,4}[-/]\d{1,4}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DMY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:uk|british|europe(?:an)?|eu)\b", re.I),
    re.compile(r"\bday\s*first\b", re.I),
    re.compile(r"\bdd\s*[/\-]\s*mm\b", re.I),
    re.compile(r"\bdmy\b", re.I),
    re.compile(r"\b(?:dd/mm|d/m)\b", re.I),
)
_MDY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:us|u\.s\.|american|usa|united\s+states)\b", re.I),
    re.compile(r"\bmonth\s*first\b", re.I),
    re.compile(r"\bmm\s*[/\-]\s*dd\b", re.I),
    re.compile(r"\bmdy\b", re.I),
    re.compile(r"\b(?:mm/dd|m/d)\b", re.I),
)

_MONTH_NAME_TO_NUMBER: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_MONTH_NAME_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)
_DATE_MAP_LINE = re.compile(
    rf"(?P<raw>\d{{1,2}}[-/]\d{{1,2}}[-/]\d{{2,4}})\s*"
    rf"(?:=|:|->|→|is|means?|should\s+be|read\s+as)\s*"
    rf"(?P<exp>[^\n;]+)",
    re.I,
)
_NAMED_DATE_DMY = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_NAME_PATTERN})\b",
    re.I,
)
_NAMED_DATE_MDY = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.I,
)


@dataclass(frozen=True)
class AmbiguousDateColumn:
    column: str
    sample_values: tuple[str, ...]


@dataclass(frozen=True)
class DateFormatClarification:
    raw_answer: str
    date_format: DateFormatPreference
    affected_columns: tuple[str, ...]
    sample_values: dict[str, tuple[str, ...]]
    question_event_id: str | None = None


@dataclass(frozen=True)
class DateAnswerInterpretation:
    resolved: DateFormatClarification | None = None
    needs_followup: bool = False
    followup_question: str = ""


def normalise_column_name(name: str) -> str:
    return name.strip().lower()


def is_known_date_column(name: str) -> bool:
    return normalise_column_name(name) in _DATE_COLUMN_NAMES


def _parse_three_part_ints(value: str) -> list[int] | None:
    parts = re.split(r"[-/]", value.strip())
    if len(parts) != 3:
        return None
    try:
        return [int(part) for part in parts]
    except ValueError:
        return None


def _day_month_parts(ints: list[int]) -> tuple[int, int] | None:
    """Return the day/month slots for ambiguity checks (ignore 4-digit year)."""
    if len(ints) != 3:
        return None
    if ints[0] >= 1000:
        return None
    if ints[2] >= 1000:
        return ints[0], ints[1]
    if ints[0] >= 100:
        return None
    return ints[0], ints[1]


def is_unambiguous_date_value(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if _ISO_DATE.match(text):
        return True
    if not _DATE_3PART.match(text):
        return True
    ints = _parse_three_part_ints(text)
    if ints is None:
        return True
    if ints[0] >= 1000:
        return True
    day_month = _day_month_parts(ints)
    if day_month is None:
        return True
    first, second = day_month
    if first > 12 or second > 12:
        return True
    return False


def is_ambiguous_date_value(value: str) -> bool:
    text = value.strip()
    if not text or _ISO_DATE.match(text):
        return False
    if not _DATE_3PART.match(text):
        return False
    ints = _parse_three_part_ints(text)
    if ints is None or ints[0] >= 1000:
        return False
    day_month = _day_month_parts(ints)
    if day_month is None:
        return False
    first, second = day_month
    return 1 <= first <= 12 and 1 <= second <= 12


def detect_ambiguous_date_columns(
    *,
    columns: list[str],
    rows: list[dict[str, str]],
    max_samples: int = 3,
) -> list[AmbiguousDateColumn]:
    """Return date columns whose non-empty values are all ambiguous slash/dash dates."""
    ambiguous: list[AmbiguousDateColumn] = []
    for column in columns:
        if not is_known_date_column(column):
            continue
        values: list[str] = []
        saw_date_like = False
        column_unambiguous = False
        for row in rows:
            raw = (row.get(column) or "").strip()
            if not raw:
                continue
            if _ISO_DATE.match(raw):
                column_unambiguous = True
                break
            if not _DATE_3PART.match(raw):
                continue
            saw_date_like = True
            if is_unambiguous_date_value(raw):
                column_unambiguous = True
                break
            if is_ambiguous_date_value(raw):
                values.append(raw)
        if column_unambiguous or not saw_date_like or not values:
            continue
        samples: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            samples.append(value)
            if len(samples) >= max_samples:
                break
        ambiguous.append(
            AmbiguousDateColumn(column=column, sample_values=tuple(samples))
        )
    return ambiguous


def read_upload_rows(path: Path, *, limit: int | None = None) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    if limit is None:
        return rows
    return rows[:limit]


def build_date_clarification_question(
    _columns: list[AmbiguousDateColumn],
) -> str:
    return (
        "Some dates in your file could mean either 5 June or 6 May. "
        "How should we read them — day first (UK) or month first (US)?"
    )


def build_followup_question() -> str:
    return (
        "We still need to know how to read your dates. "
        "Should we treat them as day first (UK) or month first (US)?"
    )


def _month_number(name: str) -> int | None:
    return _MONTH_NAME_TO_NUMBER.get(name.strip().lower())


def _parse_named_date(text: str) -> tuple[int, int] | None:
    """Return ``(day, month_number)`` from a named date fragment."""
    trimmed = text.strip()
    if not trimmed:
        return None
    dmy = _NAMED_DATE_DMY.search(trimmed)
    if dmy:
        month = _month_number(dmy.group("month"))
        day = int(dmy.group("day"))
        if month is not None and 1 <= day <= 31:
            return day, month
    mdy = _NAMED_DATE_MDY.search(trimmed)
    if mdy:
        month = _month_number(mdy.group("month"))
        day = int(mdy.group("day"))
        if month is not None and 1 <= day <= 31:
            return day, month
    return None


def _infer_format_from_raw_and_named(
    raw_value: str,
    *,
    day: int,
    month: int,
) -> DateFormatPreference | None:
    ints = _parse_three_part_ints(raw_value)
    if ints is None:
        return None
    parts = _day_month_parts(ints)
    if parts is None:
        return None
    first, second = parts
    if first == day and second == month:
        return "DMY"
    if first == month and second == day:
        return "MDY"
    return None


def _infer_format_from_date_expansions(
    answer: str,
    *,
    sample_values: dict[str, list[str]],
) -> DateFormatPreference | None:
    """Infer DMY/MDY from lines like ``05/06/2026 = 5 June 2026``."""
    dmy_votes = 0
    mdy_votes = 0

    for match in _DATE_MAP_LINE.finditer(answer):
        named = _parse_named_date(match.group("exp"))
        if named is None:
            continue
        day, month = named
        inferred = _infer_format_from_raw_and_named(
            match.group("raw"),
            day=day,
            month=month,
        )
        if inferred == "DMY":
            dmy_votes += 1
        elif inferred == "MDY":
            mdy_votes += 1

    if dmy_votes == 0 and mdy_votes == 0:
        named_dates = [_parse_named_date(fragment) for fragment in answer.splitlines()]
        named_dates.extend(_parse_named_date(match.group(0)) for match in _NAMED_DATE_DMY.finditer(answer))
        named_dates = [item for item in named_dates if item is not None]
        samples: list[str] = []
        for values in sample_values.values():
            samples.extend(values)
        for day, month in named_dates:
            for sample in samples:
                inferred = _infer_format_from_raw_and_named(
                    sample,
                    day=day,
                    month=month,
                )
                if inferred == "DMY":
                    dmy_votes += 1
                elif inferred == "MDY":
                    mdy_votes += 1

    if dmy_votes > 0 and mdy_votes > 0:
        return None
    if dmy_votes > 0:
        return "DMY"
    if mdy_votes > 0:
        return "MDY"
    return None


def interpret_date_format_answer(
    answer: str,
    *,
    affected_columns: list[str],
    sample_values: dict[str, list[str]],
) -> DateAnswerInterpretation:
    text = answer.strip()
    if not text:
        return DateAnswerInterpretation(
            needs_followup=True,
            followup_question=build_followup_question(),
        )

    dmy_hits = sum(1 for pattern in _DMY_PATTERNS if pattern.search(text))
    mdy_hits = sum(1 for pattern in _MDY_PATTERNS if pattern.search(text))
    if dmy_hits > 0 and mdy_hits > 0:
        return DateAnswerInterpretation(
            needs_followup=True,
            followup_question=build_followup_question(),
        )
    if dmy_hits > 0:
        preference: DateFormatPreference = "DMY"
    elif mdy_hits > 0:
        preference = "MDY"
    else:
        inferred = _infer_format_from_date_expansions(
            text,
            sample_values=sample_values,
        )
        if inferred is None:
            return DateAnswerInterpretation(
                needs_followup=True,
                followup_question=build_followup_question(),
            )
        preference = inferred

    samples = {
        column: tuple(values)
        for column, values in sample_values.items()
    }
    return DateAnswerInterpretation(
        resolved=DateFormatClarification(
            raw_answer=text,
            date_format=preference,
            affected_columns=tuple(affected_columns),
            sample_values=samples,
        )
    )


def _payload_kind(payload: dict[str, object]) -> str | None:
    kind = payload.get("kind")
    return kind if isinstance(kind, str) else None


def _question_payload(payload: dict[str, object]) -> dict[str, object] | None:
    if payload.get("clarification_kind") == "date_format":
        return payload
    return None


def find_resolved_date_clarification(events: list[object]) -> DateFormatClarification | None:
    for event in reversed(events):
        if event.kind != EventKind.DECISION_INPUT:
            continue
        payload = event.payload or {}
        if _payload_kind(payload) != "clarification_answered":
            continue
        date_format = payload.get("date_format")
        if date_format not in {"DMY", "MDY"}:
            continue
        columns = payload.get("affected_columns")
        if not isinstance(columns, list):
            continue
        sample_values = payload.get("sample_values")
        if not isinstance(sample_values, dict):
            sample_values = {}
        parsed_samples: dict[str, tuple[str, ...]] = {}
        for key, values in sample_values.items():
            if isinstance(key, str) and isinstance(values, list):
                parsed_samples[key] = tuple(str(v) for v in values)
        raw_answer = payload.get("raw_answer")
        return DateFormatClarification(
            raw_answer=str(raw_answer or ""),
            date_format=date_format,  # type: ignore[arg-type]
            affected_columns=tuple(str(c) for c in columns),
            sample_values=parsed_samples,
            question_event_id=(
                str(payload["question_event_id"])
                if isinstance(payload.get("question_event_id"), str)
                else None
            ),
        )
    return None


def _answered_question_ids(events: list[object]) -> set[str]:
    answered: set[str] = set()
    for event in events:
        if event.kind != EventKind.ANSWER_RECEIVED:
            continue
        qid = (event.payload or {}).get("question_event_id")
        if isinstance(qid, str):
            answered.add(qid)
    return answered


def find_open_date_clarification_question(
    events: list[object],
) -> tuple[object | None, dict[str, object] | None]:
    answered = _answered_question_ids(events)
    for event in reversed(events):
        if event.kind != EventKind.QUESTION_ASKED:
            continue
        payload = event.payload or {}
        if _question_payload(payload) is None:
            continue
        if str(event.id) in answered:
            continue
        return event, payload
    return None, None


def find_latest_unprocessed_date_answer(
    events: list[object],
) -> tuple[object | None, dict[str, object] | None, object | None]:
    if find_resolved_date_clarification(events) is not None:
        return None, None, None

    answered = _answered_question_ids(events)
    question_by_id: dict[str, tuple[object, dict[str, object]]] = {}
    for event in events:
        if event.kind != EventKind.QUESTION_ASKED:
            continue
        payload = event.payload or {}
        if _question_payload(payload) is None:
            continue
        question_by_id[str(event.id)] = (event, payload)

    processed_questions: set[str] = set()
    for event in events:
        if event.kind != EventKind.DECISION_INPUT:
            continue
        payload = event.payload or {}
        kind = _payload_kind(payload)
        if kind not in {"clarification_answered", "clarification_needs_followup"}:
            continue
        qid = payload.get("question_event_id")
        if isinstance(qid, str):
            processed_questions.add(qid)

    latest_answer = None
    for event in reversed(events):
        if event.kind != EventKind.ANSWER_RECEIVED:
            continue
        qid = (event.payload or {}).get("question_event_id")
        if not isinstance(qid, str) or qid not in question_by_id:
            continue
        if qid in processed_questions:
            continue
        latest_answer = event
        break

    if latest_answer is None:
        return None, None, None

    qid = str((latest_answer.payload or {}).get("question_event_id"))
    question_event, question_payload = question_by_id[qid]
    return latest_answer, question_payload, question_event


def clarification_to_payload(clarification: DateFormatClarification) -> dict[str, object]:
    return {
        "raw_answer": clarification.raw_answer,
        "date_format": clarification.date_format,
        "affected_columns": list(clarification.affected_columns),
        "sample_values": {
            column: list(values)
            for column, values in clarification.sample_values.items()
        },
        "question_event_id": clarification.question_event_id,
    }


def process_pending_date_clarification_answer(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
) -> SessionStatus | None:
    """Interpret the latest date-clarification answer and emit decision events."""
    events = event_log.read_all(session_id)
    if find_resolved_date_clarification(events) is not None:
        return None

    answer_event, question_payload, question_event = find_latest_unprocessed_date_answer(
        events
    )
    if answer_event is None or question_payload is None:
        return None

    interpretation = interpret_date_format_answer(
        str((answer_event.payload or {}).get("answer") or ""),
        affected_columns=list(question_payload.get("affected_columns") or []),
        sample_values={
            str(key): list(value)
            for key, value in (question_payload.get("sample_values") or {}).items()
            if isinstance(key, str) and isinstance(value, list)
        },
    )
    question_id = str(question_event.id) if question_event is not None else None
    if interpretation.needs_followup:
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "clarification_needs_followup",
                "clarification_kind": "date_format",
                "question_event_id": question_id,
                "raw_answer": str((answer_event.payload or {}).get("answer") or ""),
                "reason": "Answer did not clearly specify DMY or MDY.",
            },
            step=step,
        )
        event_log.append(
            session_id=session_id,
            kind=EventKind.QUESTION_ASKED,
            actor_type=ActorType.SYSTEM,
            payload={
                "plain_english_question": interpretation.followup_question,
                "technical_context": "Date format clarification follow-up.",
                "clarification_kind": "date_format",
                "affected_columns": question_payload.get("affected_columns") or [],
                "sample_values": question_payload.get("sample_values") or {},
            },
            step=step,
        )
        return SessionStatus.PAUSED_USER

    if interpretation.resolved is None:
        return None

    resolved_payload = clarification_to_payload(
        DateFormatClarification(
            raw_answer=interpretation.resolved.raw_answer,
            date_format=interpretation.resolved.date_format,
            affected_columns=interpretation.resolved.affected_columns,
            sample_values=interpretation.resolved.sample_values,
            question_event_id=question_id,
        )
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={"kind": "clarification_answered", **resolved_payload},
        step=step,
    )
    return None


def apply_date_format_clarification_gate(
    *,
    session_id: UUID,
    event_log: EventLog,
    upload_path: Path,
    columns: list[str],
    step: int,
    stage_label: str,
) -> SessionStatus | None:
    """Pause on ``paused_user`` when date clarification is required or pending."""
    events = event_log.read_all(session_id)
    resolved = find_resolved_date_clarification(events)
    if resolved is not None:
        return None

    processed = process_pending_date_clarification_answer(
        session_id=session_id,
        event_log=event_log,
        step=step,
    )
    if processed is not None:
        return processed
    if find_resolved_date_clarification(event_log.read_all(session_id)) is not None:
        return None

    open_question, _open_payload = find_open_date_clarification_question(
        event_log.read_all(session_id)
    )
    if open_question is not None:
        return SessionStatus.PAUSED_USER

    rows = read_upload_rows(upload_path)
    ambiguous = detect_ambiguous_date_columns(columns=columns, rows=rows)
    if not ambiguous:
        return None

    affected = [item.column for item in ambiguous]
    sample_map = {item.column: list(item.sample_values) for item in ambiguous}
    question_text = build_date_clarification_question(ambiguous)
    question_id = str(uuid4())
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "date_clarification_required",
            "stage": stage_label,
            "clarification_kind": "date_format",
            "affected_columns": affected,
            "sample_values": sample_map,
        },
        step=step,
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.QUESTION_ASKED,
        actor_type=ActorType.SYSTEM,
        payload={
            "question_event_id": question_id,
            "plain_english_question": question_text,
            "technical_context": (
                "Ambiguous slash/dash dates detected before model authoring."
            ),
            "clarification_kind": "date_format",
            "affected_columns": affected,
            "sample_values": sample_map,
        },
        step=step,
    )
    return SessionStatus.PAUSED_USER


def date_clarification_prompt_section(
    clarification: dict[str, object] | None,
) -> dict[str, object]:
    if not clarification:
        return {}
    date_format = clarification.get("date_format")
    if date_format not in {"DMY", "MDY"}:
        return {}
    affected = clarification.get("affected_columns")
    if not isinstance(affected, list):
        affected = []
    return {
        "date_format_clarification": {
            "date_format": date_format,
            "affected_columns": affected,
            "raw_answer": clarification.get("raw_answer"),
            "sample_values": clarification.get("sample_values") or {},
            "instruction": (
                "The user confirmed date parsing preference before authoring. "
                f"Parse affected date columns using {date_format} "
                f"({'day/month/year' if date_format == 'DMY' else 'month/day/year'}). "
                "Do not guess a different format."
            ),
        }
    }


__all__ = [
    "AmbiguousDateColumn",
    "DateAnswerInterpretation",
    "DateFormatClarification",
    "DateFormatPreference",
    "apply_date_format_clarification_gate",
    "build_date_clarification_question",
    "build_followup_question",
    "clarification_to_payload",
    "date_clarification_prompt_section",
    "detect_ambiguous_date_columns",
    "find_open_date_clarification_question",
    "find_resolved_date_clarification",
    "interpret_date_format_answer",
    "process_pending_date_clarification_answer",
    "is_ambiguous_date_value",
    "is_known_date_column",
    "is_unambiguous_date_value",
    "read_upload_rows",
]
