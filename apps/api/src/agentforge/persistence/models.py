"""SQLAlchemy ORM models — Phase 1 baseline.

These mirror the Pydantic schemas in ``agentforge.schemas`` for the
entities that need indexable structured storage. Per-session chronological
events live in ``events.jsonl``, not in the DB.

Phase 1 covers the canonical entities so the baseline migration is complete.
Subsequent prompts add specific repositories on top of these models.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentforge.persistence.db import Base


class SessionRow(Base):
    """Indexable session row. Authoritative for status + phase + budgets."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Budgets (stored flat for easy indexing + admin queries)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    steps_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wall_seconds_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Terminal info
    terminal_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Dashboard lifecycle (archive / soft-delete / restore)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status_before_archive: Mapped[str | None] = mapped_column(String(32), nullable=True)

    uploaded_files: Mapped[list[UploadedFileRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[DecisionRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list[ApprovalRequestRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    tool_invocations: Mapped[list[ToolInvocationRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[ArtifactRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    model_calls: Mapped[list[ModelCallRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class UploadedFileRow(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    session: Mapped[SessionRow] = relationship(back_populates="uploaded_files")


class DecisionRow(Base):
    """Q&A history per session."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    session: Mapped[SessionRow] = relationship(back_populates="decisions")


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    business_summary: Mapped[str] = mapped_column(Text, nullable=False)
    diff_paths_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    session: Mapped[SessionRow] = relationship(back_populates="approval_requests")
    decisions: Mapped[list[ApprovalDecisionRow]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("approval_requests.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(128), nullable=False)

    request: Mapped[ApprovalRequestRow] = relationship(back_populates="decisions")


class ToolInvocationRow(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    session: Mapped[SessionRow] = relationship(back_populates="tool_invocations")


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    session: Mapped[SessionRow] = relationship(back_populates="artifacts")


class ModelCallRow(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    session: Mapped[SessionRow] = relationship(back_populates="model_calls")


class IdempotencyKeyRow(Base):
    """Cache for tool-call idempotency."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class EvalRunRow(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, nullable=False)

    results: Mapped[list[EvalResultRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class EvalResultRow(Base):
    __tablename__ = "eval_results"
    __table_args__ = (UniqueConstraint("run_id", "scenario_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_runs.id"), nullable=False, index=True
    )
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(default=0.0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[EvalRunRow] = relationship(back_populates="results")
