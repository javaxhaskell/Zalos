"""Artifact and execution-observation schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from agentforge.schemas.common import StrictModel


class ArtifactType(StrEnum):
    UPLOADED_FILE = "uploaded_file"
    GENERATED_CODE = "generated_code"
    OUTPUT_FILE = "output_file"
    WORKFLOW_ROW_OUTPUT = "workflow_row_output"
    WORKFLOW_REPORT = "workflow_report"
    SYSTEM_VALIDATION_REPORT = "system_validation_report"
    VALIDATION_REPORT = "validation_report"
    REPAIR_REPORT = "repair_report"
    ARCHIVE = "archive"


class Artifact(StrictModel):
    """Any downloadable file produced by or attached to a session."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    type: ArtifactType
    path: str
    """Relative to the session workspace."""
    hash: str
    """sha256 of the file content."""
    size_bytes: int
    created_at: datetime


class UploadedFile(StrictModel):
    """An uploaded CSV/XLSX in ``uploads/``."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    filename: str
    mime: str
    size_bytes: int
    hash_sha256: str
    storage_path: str
    uploaded_at: datetime


class ExecutionObservation(StrictModel):
    """Result of ``run_python_script`` or any subprocess invocation."""

    invocation_id: UUID
    success: bool
    exit_code: int | None = None
    stdout_excerpt: str
    """Up to 1 MiB; overflow written to ``outputs/_logs/{step}.log``."""
    stderr_excerpt: str
    files_written: list[str] = Field(default_factory=list)
    """Workspace-relative paths."""
    latency_ms: int
    truncated: bool = False
    overflow_log_path: str | None = None


class PerTestResult(StrictModel):
    """Per-test outcome surfaced in TestResultsPanel."""

    name: str
    status: str
    """'passed' | 'failed' | 'skipped' | 'error'."""
    humanised_name: str
    """Human-friendly rendering shown to finance users."""
    latency_ms: int
    output_excerpt: str | None = None


class TestResults(StrictModel):
    """pytest summary + per-test detail."""

    invocation_id: UUID
    passed_count: int
    failed_count: int
    skipped_count: int = 0
    error_count: int = 0
    total_count: int
    collected_count: int = 0
    """Number of tests pytest discovered during collection."""
    summary_line: str
    per_test: list[PerTestResult] = Field(default_factory=list)
    raw_output_excerpt: str
