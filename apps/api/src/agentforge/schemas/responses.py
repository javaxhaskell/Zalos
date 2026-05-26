"""HTTP response wrappers built from the canonical schemas.

These are *boundary* shapes for the API; they compose canonical entities
into the exact JSON the frontend receives. Keeping them in a separate
module makes the OpenAPI snapshot stable and reviewable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from agentforge.schemas.common import ApprovalStatus, SessionStatus, StrictModel
from agentforge.schemas.event import WorkspaceEvent
from agentforge.schemas.session import ResumeManifest


class SessionEventsResponse(StrictModel):
    """GET /sessions/{id}/events response."""

    session_id: UUID
    events: list[WorkspaceEvent]
    """Chronological — oldest first. ``after`` query param filters strictly after that id."""
    has_more: bool = False
    """True if there are more events than fit in this page (Phase 3 returns all; reserved for later)."""


class ChainCheck(StrictModel):
    """Light chain-integrity check (production extension upgrades this to a signed hash chain)."""

    valid: bool
    message: str | None = None


class AuditExportResponse(StrictModel):
    """GET /audit/export/{session_id} response.

    The full append-only event log plus the manifest, so a downstream
    auditor can reconstruct the session without consulting other sources.
    """

    session_id: UUID
    exported_at: datetime
    manifest: ResumeManifest
    events: list[WorkspaceEvent]
    event_count: int
    chain_check: ChainCheck


class FileUploadResponse(StrictModel):
    """POST /sessions/{id}/files response."""

    uploaded_file_id: UUID
    filename: str
    size_bytes: int
    hash_sha256: str
    relative_path: str
    uploaded_at: datetime


# ---------------------------------------------------------------------------
# Approval action bodies (POST /sessions/{id}/approve and /reject)
# ---------------------------------------------------------------------------


class ApprovalGrantRequest(StrictModel):
    """POST /sessions/{id}/approve body.

    The ``request_id`` matches the ``request_id`` field in the
    ``APPROVAL_REQUESTED`` event payload emitted by the agent loop
    when it paused. ``decided_by`` records the actor; for the single-
    user demo it defaults to the demo user id.
    """

    request_id: UUID
    decided_by: str = "user-demo"


class ApprovalDeclineRequest(StrictModel):
    """POST /sessions/{id}/reject body.

    Per ADR-0006, decline requires a ``reason``; the UI surfaces this
    in the ApprovalPanel's free-text field. Empty strings are rejected
    at the boundary.
    """

    request_id: UUID
    reason: str = Field(min_length=1, max_length=2000)
    decided_by: str = "user-demo"


class ApprovalActionResponse(StrictModel):
    """Common response for /approve and /reject."""

    session_id: UUID
    request_id: UUID
    status: ApprovalStatus
    decision_id: UUID
    event_id: UUID
    """ID of the ``APPROVAL_GRANTED`` or ``APPROVAL_DECLINED`` event
    that resumes the loop on next ``AgentLoop.run`` call."""
    decided_at: datetime


# ---------------------------------------------------------------------------
# Session run / answer / finalise (BP8)
# ---------------------------------------------------------------------------


class SessionRunRequest(StrictModel):
    """POST /sessions/{id}/run body.

    The orchestrator translates this into the first ``ModelMessage`` the
    agent loop sees. ``user_message`` is the workflow description (author
    flow) or the problem report (repair flow). ``template_hint`` is a
    soft signal — recorded as a ``DECISION_INPUT`` event so the model
    sees which template the user picked from the wizard.
    """

    user_message: str | None = Field(
        default=None,
        max_length=8_000,
        description=(
            "Free-text description from the wizard's textarea. Author "
            "flow: the workflow description. Repair flow: the problem "
            "report. Wrapped in <user_message> delimiters before being "
            "passed to the model (INV-10)."
        ),
    )
    template_hint: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Optional template name (e.g. 'bank_categoriser') from the "
            "wizard's template picker. Recorded as a DECISION_INPUT "
            "event so the model knows which template to seed."
        ),
    )


class SessionRunResponse(StrictModel):
    """POST /sessions/{id}/run response (202 Accepted).

    The orchestrator runs on a background asyncio task; the client polls
    /events to follow progress and re-fetches /sessions/{id} once a
    terminal status appears.
    """

    session_id: UUID
    status: SessionStatus
    """Always ``running`` on successful spawn. The /events polling loop
    transitions to the terminal status (completed / failed_*) when the
    flow finishes."""
    started_at: datetime


class SessionAnswerRequest(StrictModel):
    """POST /sessions/{id}/answer body.

    Used when the agent loop has emitted a ``question_asked`` event and
    is paused on ``paused_user``. The answer is recorded as an
    ``answer_received`` event so the next loop invocation can resume.
    """

    answer: str = Field(min_length=1, max_length=4_000)


class SessionAnswerResponse(StrictModel):
    """POST /sessions/{id}/answer response."""

    session_id: UUID
    event_id: UUID
    accepted_at: datetime


class SessionFinaliseRequest(StrictModel):
    """POST /sessions/{id}/finalise body."""

    summary: str | None = Field(default=None, max_length=1_000)


class SessionFinaliseResponse(StrictModel):
    """POST /sessions/{id}/finalise response.

    Mirrors the in-loop ``finalise_session`` tool semantics: refuses
    without a recorded ``ARTIFACT_GENERATED`` event, otherwise marks
    the session ``completed`` in both the manifest and the DB row.
    """

    session_id: UUID
    status: SessionStatus
    finalised_at: datetime
    event_id: UUID


class SessionArchiveResponse(StrictModel):
    """POST /sessions/{id}/archive response."""

    session_id: UUID
    status: SessionStatus
    archived_at: datetime
    event_id: UUID


class SessionRestoreResponse(StrictModel):
    """POST /sessions/{id}/restore response."""

    session_id: UUID
    status: SessionStatus
    restored_at: datetime


class SessionCancelResponse(StrictModel):
    """POST /sessions/{id}/cancel response."""

    session_id: UUID
    status: SessionStatus
    terminal_error_code: str | None = None
    cancelled_at: datetime
    event_id: UUID | None = None
    """ID of the ``WORKFLOW_FAILED`` event when one was appended."""


# ---------------------------------------------------------------------------
# Fixture loading (BP9) — bundled broken-agent fixtures for the repair demo
# ---------------------------------------------------------------------------


class LoadFixtureResponse(StrictModel):
    """POST /sessions/{id}/load_fixture/{name} response.

    The repair wizard's InputStage uses this so a demo run does not
    require the user to package their own ZIP. Only the bundled
    fixtures under :attr:`Settings.fixtures_broken_agents_root` can be
    loaded; arbitrary paths are rejected at the boundary (INV-5 — the
    workspace path discipline applies symmetrically to writes from
    HTTP-side helpers).
    """

    session_id: UUID
    fixture_name: str
    files_copied: list[str]
    """Workspace-relative paths the loader wrote (e.g. ``working/agent.py``)."""
    staged_golden_path: str | None = None
    """If the fixture ships ``data/expected_output.csv``, it is copied
    to ``evals/expected_output.csv`` so ``validate_output`` can find it.
    Reported here so the wizard can confirm the golden is in place."""
    loaded_at: datetime


class UploadAgentZipResponse(StrictModel):
    """POST /sessions/{id}/upload_agent_zip response.

    The repair wizard's InputStage offers ZIP upload as an alternative
    to the bundled-fixture picker — the user packages their own broken
    agent + problem report into a zip, the backend extracts it into
    ``${workspace}/working/`` (with zip-slip + size + file-count
    defences), and the repair flow runs against it the same way it
    runs against a bundled fixture.

    INV-5: every extracted entry is resolved through
    :meth:`WorkspaceManager.resolve_in`; entries that would escape
    ``working/`` are rejected, the partial extraction is rolled back,
    and the call returns 400.
    """

    session_id: UUID
    archive_filename: str
    files_extracted: list[str]
    """Workspace-relative paths the extractor wrote."""
    staged_golden_path: str | None = None
    """If the ZIP contains ``data/expected_output.csv``, it is copied
    to ``evals/expected_output.csv`` so ``validate_output`` can find it.
    Reported here so the wizard can confirm the golden is in place."""
    total_uncompressed_bytes: int
    uploaded_at: datetime
