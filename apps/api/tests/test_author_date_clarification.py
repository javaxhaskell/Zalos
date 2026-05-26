"""Tests for Author date-format clarification (pre-model-authoring gate)."""

from __future__ import annotations

import csv
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agentforge.orchestrator.author_date_clarification import (
    apply_date_format_clarification_gate,
    build_date_clarification_question,
    detect_ambiguous_date_columns,
    find_resolved_date_clarification,
    interpret_date_format_answer,
    is_ambiguous_date_value,
    is_unambiguous_date_value,
)
from agentforge.persistence.event_log import EventLog
from agentforge.schemas import ActorType, EventKind, SessionStatus, Workflow
from agentforge.persistence.workspace import WorkspaceManager

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INVOICE_SAMPLE = (
    _REPO_ROOT
    / "apps/web/app/api/reference-samples/invoice-aging/invoice_aging_cleanup_demo.csv"
)


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def test_unambiguous_when_day_part_exceeds_twelve() -> None:
    assert is_unambiguous_date_value("13/06/2026")
    assert not is_ambiguous_date_value("13/06/2026")


def test_unambiguous_iso_dates() -> None:
    assert is_unambiguous_date_value("2026-05-06")
    assert not is_ambiguous_date_value("2026-05-06")


def test_ambiguous_slash_dates() -> None:
    assert is_ambiguous_date_value("05/06/2026")
    assert is_ambiguous_date_value("04/03/2026")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("We use UK format, day first / DD/MM/YYYY", "DMY"),
        ("American dates — month first MM/DD", "MDY"),
        ("British invoice dates are DMY", "DMY"),
        ("US month/day/year", "MDY"),
        (
            "05/06/2026 = 5 June 2026\n"
            "04/03/2026 = 4 March 2026\n"
            "03/07/2026 = 3 July 2026",
            "DMY",
        ),
        ("05/06/2026 = 6 May 2026", "MDY"),
        ("5 June, 4 March, 3 July", "DMY"),
    ],
)
def test_interpret_obvious_answers(answer: str, expected: str) -> None:
    result = interpret_date_format_answer(
        answer,
        affected_columns=["invoice_date"],
        sample_values={"invoice_date": ["05/06/2026"]},
    )
    assert result.needs_followup is False
    assert result.resolved is not None
    assert result.resolved.date_format == expected


@pytest.mark.parametrize(
    "answer",
    [
        "yes",
        "continue",
        "not sure",
        "UK and US mixed",
    ],
)
def test_interpret_unclear_answers_requests_followup(answer: str) -> None:
    result = interpret_date_format_answer(
        answer,
        affected_columns=["invoice_date"],
        sample_values={"invoice_date": ["05/06/2026"]},
    )
    assert result.needs_followup is True
    assert result.resolved is None


def test_detect_ambiguous_columns_on_invoice_sample() -> None:
    assert _INVOICE_SAMPLE.is_file(), "invoice aging demo sample must exist"
    with _INVOICE_SAMPLE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    columns = list(rows[0].keys()) if rows else []
    ambiguous = detect_ambiguous_date_columns(columns=columns, rows=rows)
    names = {item.column for item in ambiguous}
    assert "invoice_date" in names
    assert "due_date" in names
    assert all(item.sample_values for item in ambiguous)


def test_question_is_finance_friendly_without_technical_markers() -> None:
    ambiguous = detect_ambiguous_date_columns(
        columns=["invoice_date", "due_date"],
        rows=[
            {"invoice_date": "05/06/2026", "due_date": "04/03/2026"},
            {"invoice_date": "03/07/2026", "due_date": "02/05/2026"},
        ],
    )
    question = build_date_clarification_question(ambiguous)
    assert "05/06/2026" not in question
    assert "invoice_date" not in question
    assert "due_date" not in question
    assert "`" not in question
    assert "Reply in your own words" not in question
    assert "day/month/year" not in question
    assert "month/day/year" not in question
    assert "5 June or 6 May" in question
    assert "day first (UK)" in question
    assert "month first (US)" in question


def test_question_includes_sample_values_not_fixed_options() -> None:
    ambiguous = detect_ambiguous_date_columns(
        columns=["invoice_date"],
        rows=[{"invoice_date": "05/06/2026"}, {"invoice_date": "04/03/2026"}],
    )
    question = build_date_clarification_question(ambiguous)
    assert "05/06/2026" not in question
    assert "radio" not in question.lower()
    assert "dropdown" not in question.lower()


def test_gate_pauses_before_model_authoring(
    workspaces_root: Path,
) -> None:
    session_id = uuid4()
    manager = WorkspaceManager(root=workspaces_root)
    manager.allocate(session_id, Workflow.AUTHOR)
    workspace = manager.get(session_id)
    upload_path = workspace / "uploads" / "input.csv"
    _write_csv(
        upload_path,
        ["invoice_id", "invoice_date", "amount"],
        [
            {"invoice_id": "INV-1", "invoice_date": "05/06/2026", "amount": "100"},
            {"invoice_id": "INV-2", "invoice_date": "04/03/2026", "amount": "200"},
        ],
    )
    event_log = EventLog(manager)
    status = apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=0,
        stage_label="test_gate",
    )
    assert status == SessionStatus.PAUSED_USER
    events = event_log.read_all(session_id)
    assert any(
        event.kind == EventKind.QUESTION_ASKED
        and (event.payload or {}).get("clarification_kind") == "date_format"
        for event in events
    )
    question = next(
        event
        for event in reversed(events)
        if event.kind == EventKind.QUESTION_ASKED
    )
    payload = question.payload or {}
    assert isinstance(payload.get("sample_values"), dict)
    assert "invoice_date" in payload["sample_values"]
    assert payload["sample_values"]["invoice_date"]


def test_gate_resolves_answer_and_emits_clarification_answered(
    workspaces_root: Path,
) -> None:
    session_id = uuid4()
    manager = WorkspaceManager(root=workspaces_root)
    manager.allocate(session_id, Workflow.AUTHOR)
    workspace = manager.get(session_id)
    upload_path = workspace / "uploads" / "input.csv"
    _write_csv(
        upload_path,
        ["invoice_id", "invoice_date", "amount"],
        [{"invoice_id": "INV-1", "invoice_date": "05/06/2026", "amount": "100"}],
    )
    event_log = EventLog(manager)
    apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=0,
        stage_label="test_gate",
    )
    events = event_log.read_all(session_id)
    question = next(
        event for event in reversed(events) if event.kind == EventKind.QUESTION_ASKED
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.ANSWER_RECEIVED,
        actor_type=ActorType.USER,
        payload={
            "answer": "UK day-first DD/MM/YYYY",
            "question_event_id": str(question.id),
        },
        step=0,
    )
    status = apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=1,
        stage_label="test_gate_resume",
    )
    assert status is None
    resolved = find_resolved_date_clarification(event_log.read_all(session_id))
    assert resolved is not None
    assert resolved.date_format == "DMY"
    assert resolved.affected_columns == ("invoice_date",)


def test_gate_resolves_date_expansion_answer(workspaces_root: Path) -> None:
    session_id = uuid4()
    manager = WorkspaceManager(root=workspaces_root)
    manager.allocate(session_id, Workflow.AUTHOR)
    workspace = manager.get(session_id)
    upload_path = workspace / "uploads" / "input.csv"
    _write_csv(
        upload_path,
        ["invoice_id", "invoice_date", "amount"],
        [{"invoice_id": "INV-1", "invoice_date": "05/06/2026", "amount": "100"}],
    )
    event_log = EventLog(manager)
    apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=0,
        stage_label="test_gate",
    )
    question = next(
        event
        for event in reversed(event_log.read_all(session_id))
        if event.kind == EventKind.QUESTION_ASKED
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.ANSWER_RECEIVED,
        actor_type=ActorType.USER,
        payload={
            "answer": (
                "05/06/2026 = 5 June 2026\n"
                "04/03/2026 = 4 March 2026\n"
                "03/07/2026 = 3 July 2026"
            ),
            "question_event_id": str(question.id),
        },
        step=0,
    )
    status = apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=1,
        stage_label="test_gate_resume",
    )
    assert status is None
    resolved = find_resolved_date_clarification(event_log.read_all(session_id))
    assert resolved is not None
    assert resolved.date_format == "DMY"


def test_answer_endpoint_processes_date_expansion_answer(app_client) -> None:
    from agentforge.persistence.workspace import WorkspaceManager

    session = app_client.post("/sessions", json={"workflow": "author"}).json()
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    question = log.append(
        session_id=UUID(sid),
        kind=EventKind.QUESTION_ASKED,
        actor_type=ActorType.SYSTEM,
        payload={
            "plain_english_question": "How should we read your dates?",
            "clarification_kind": "date_format",
            "affected_columns": ["invoice_date"],
            "sample_values": {"invoice_date": ["05/06/2026", "04/03/2026"]},
        },
        step=2,
    )

    resp = app_client.post(
        f"/sessions/{sid}/answer",
        json={
            "answer": (
                "05/06/2026 = 5 June 2026\n"
                "04/03/2026 = 4 March 2026"
            ),
        },
    )
    assert resp.status_code == 200, resp.text

    events = app_client.get(f"/sessions/{sid}/events").json()["events"]
    assert any(
        event["kind"] == "decision_input"
        and (event.get("payload") or {}).get("kind") == "clarification_answered"
        and (event.get("payload") or {}).get("date_format") == "DMY"
        for event in events
    )
    answers = [event for event in events if event["kind"] == "answer_received"]
    assert answers[-1]["payload"]["question_event_id"] == str(question.id)


def test_gate_emits_followup_for_unclear_answer(
    workspaces_root: Path,
) -> None:
    session_id = uuid4()
    manager = WorkspaceManager(root=workspaces_root)
    manager.allocate(session_id, Workflow.AUTHOR)
    workspace = manager.get(session_id)
    upload_path = workspace / "uploads" / "input.csv"
    _write_csv(
        upload_path,
        ["invoice_id", "invoice_date", "amount"],
        [{"invoice_id": "INV-1", "invoice_date": "05/06/2026", "amount": "100"}],
    )
    event_log = EventLog(manager)
    apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=0,
        stage_label="test_gate",
    )
    question = next(
        event
        for event in reversed(event_log.read_all(session_id))
        if event.kind == EventKind.QUESTION_ASKED
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.ANSWER_RECEIVED,
        actor_type=ActorType.USER,
        payload={"answer": "maybe", "question_event_id": str(question.id)},
        step=0,
    )
    status = apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload_path,
        columns=["invoice_id", "invoice_date", "amount"],
        step=1,
        stage_label="test_gate_followup",
    )
    assert status == SessionStatus.PAUSED_USER
    events = event_log.read_all(session_id)
    assert any(
        event.kind == EventKind.DECISION_INPUT
        and (event.payload or {}).get("kind") == "clarification_needs_followup"
        for event in events
    )


def test_compact_schema_profile_includes_clarification() -> None:
    from agentforge.orchestrator.author_llm_authoring import _compact_schema_profile

    compact = _compact_schema_profile(
        {
            "columns": ["invoice_date"],
            "row_count": 1,
            "date_format_clarification": {
                "date_format": "MDY",
                "affected_columns": ["invoice_date"],
                "raw_answer": "US month first",
                "sample_values": {"invoice_date": ["05/06/2026"]},
            },
        }
    )
    assert compact["date_format_clarification"]["date_format"] == "MDY"
    assert "Do not guess" in compact["date_format_clarification"]["instruction"]


def test_skips_clarification_for_unambiguous_upload(tmp_path: Path) -> None:
    upload_path = tmp_path / "input.csv"
    _write_csv(
        upload_path,
        ["invoice_id", "invoice_date"],
        [
            {"invoice_id": "INV-1", "invoice_date": "2026-05-06"},
            {"invoice_id": "INV-2", "invoice_date": "13/06/2026"},
        ],
    )
    with upload_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    ambiguous = detect_ambiguous_date_columns(
        columns=["invoice_id", "invoice_date"],
        rows=rows,
    )
    assert ambiguous == []


def test_question_payload_has_no_fixed_answer_options() -> None:
    ambiguous = detect_ambiguous_date_columns(
        columns=["invoice_date"],
        rows=[{"invoice_date": "05/06/2026"}],
    )
    question = build_date_clarification_question(ambiguous)
    lowered = question.lower()
    for forbidden in ("option a", "option b", "click", "select one"):
        assert forbidden not in lowered


def test_http_author_run_pauses_on_ambiguous_invoice_dates(
    app_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import time

    from agentforge.models import FakeModelClient

    monkeypatch.setattr(
        "agentforge.api.deps.get_model_client",
        lambda: FakeModelClient(script=[]),
    )
    session = app_client.post("/sessions", json={"workflow": "author"}).json()
    sid = session["id"]
    sample = _INVOICE_SAMPLE.read_bytes()
    upload = app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("invoice_aging_cleanup_demo.csv", io.BytesIO(sample), "text/csv")},
    )
    assert upload.status_code == 201
    run = app_client.post(
        f"/sessions/{sid}/run",
        json={
            "user_message": (
                "Build an invoice aging cleanup agent. Assign aging buckets from invoice_date."
            ),
        },
    )
    assert run.status_code == 202

    deadline = time.monotonic() + 30.0
    events: list[dict] = []
    while time.monotonic() < deadline:
        resp = app_client.get(f"/sessions/{sid}/events")
        events = resp.json()["events"]
        session_resp = app_client.get(f"/sessions/{sid}")
        if session_resp.json()["status"] == "paused_user":
            break
        time.sleep(0.2)

    assert app_client.get(f"/sessions/{sid}").json()["status"] == "paused_user"
    question = next(
        event for event in reversed(events) if event["kind"] == "question_asked"
    )
    payload = question["payload"]
    assert payload.get("clarification_kind") == "date_format"
    assert "invoice_date" in (payload.get("sample_values") or {})
    assert "05/06/2026" in payload["sample_values"]["invoice_date"]
    assert "05/06/2026" not in payload["plain_english_question"]
    assert "day first (UK)" in payload["plain_english_question"]
