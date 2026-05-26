"""session soft-delete + archive restore metadata

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25

Adds ``deleted_at`` for recently-deleted dashboard view and
``status_before_archive`` so archived sessions can be restored.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("status_before_archive", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_sessions_deleted_at", "sessions", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_sessions_deleted_at", table_name="sessions")
    op.drop_column("sessions", "status_before_archive")
    op.drop_column("sessions", "deleted_at")
