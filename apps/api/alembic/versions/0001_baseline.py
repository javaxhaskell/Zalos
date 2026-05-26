"""baseline — all Phase 1 tables

Revision ID: 0001
Revises:
Create Date: 2026-05-21

Creates the canonical entities needed by Phase 1 contracts:
sessions, uploaded_files, decisions, approval_requests, approval_decisions,
tool_invocations, artifacts, model_calls, idempotency_keys, eval_runs, eval_results.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workflow", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_phase", sa.String(length=32), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wall_seconds_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("terminal_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_event_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_sessions_workflow", "sessions", ["workflow"])
    op.create_index("ix_sessions_status", "sessions", ["status"])

    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("hash_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_uploaded_files_session", "uploaded_files", ["session_id"])

    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options_json", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("asked_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_decisions_session", "decisions", ["session_id"])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("business_summary", sa.Text(), nullable=False),
        sa.Column("diff_paths_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_approval_requests_session", "approval_requests", ["session_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])

    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("approval_requests.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
    )
    op.create_index("ix_approval_decisions_request", "approval_decisions", ["request_id"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_tool_invocations_session", "tool_invocations", ["session_id"])
    op.create_index("ix_tool_invocations_tool_name", "tool_invocations", ["tool_name"])
    op.create_index("ix_tool_invocations_idempotency", "tool_invocations", ["idempotency_key"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_artifacts_session", "artifacts", ["session_id"])
    op.create_index("ix_artifacts_type", "artifacts", ["type"])

    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_model_calls_session", "model_calls", ["session_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("eval_runs.id"), nullable=False),
        sa.Column("scenario_id", sa.String(length=64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("diff_path", sa.Text(), nullable=True),
        sa.UniqueConstraint("run_id", "scenario_id", name="uq_eval_result_run_scenario"),
    )
    op.create_index("ix_eval_results_run", "eval_results", ["run_id"])
    op.create_index("ix_eval_results_scenario", "eval_results", ["scenario_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_results_scenario", table_name="eval_results")
    op.drop_index("ix_eval_results_run", table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_model_calls_session", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("ix_artifacts_type", table_name="artifacts")
    op.drop_index("ix_artifacts_session", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_tool_invocations_idempotency", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_tool_name", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_session", table_name="tool_invocations")
    op.drop_table("tool_invocations")
    op.drop_index("ix_approval_decisions_request", table_name="approval_decisions")
    op.drop_table("approval_decisions")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_session", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_decisions_session", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_uploaded_files_session", table_name="uploaded_files")
    op.drop_table("uploaded_files")
    op.drop_index("ix_sessions_status", table_name="sessions")
    op.drop_index("ix_sessions_workflow", table_name="sessions")
    op.drop_table("sessions")
