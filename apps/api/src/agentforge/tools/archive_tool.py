"""``archive_workspace`` tool (BP10a).

Bundles the load-bearing workspace artifacts into ``archive.zip`` and
emits an ``ARTIFACT_GENERATED`` event with ``artifact_type=ARCHIVE``.
The model dispatches this from the BUILD/FIX phase before — or
instead of — calling ``finalise_session``.

Per ADR-0006 the underlying I/O is bounded (destination is within
the workspace, source is the workspace), but this is still a write
that produces a download surface for the user, so INV-4's default
``requires_approval=True`` stays in place. The
existing orchestrator covering grants (in ``AuthorFlow`` /
``RepairFlow``) cover it once the FakeModelClient scripts list it
alongside ``finalise_session``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentforge.persistence.archive import ArchiveResult, build_archive
from agentforge.schemas import (
    ActorType,
    ArtifactType,
    EventKind,
    RiskLevel,
    StrictModel,
    ToolDefinition,
    ToolPhase,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"


class ArchiveWorkspaceInput(StrictModel):
    """Arguments for ``archive_workspace``.

    The tool builds a zip of every load-bearing workspace artifact;
    there are no input knobs today. A future extension might accept
    an ``include_uploads: bool`` toggle, but that would land via ADR
    + a new field, not by silently flipping the default.
    """

    summary: str = ""
    """Optional one-line note recorded in the ARTIFACT_GENERATED event."""


class ArchiveWorkspaceOutput(StrictModel):
    """Result of ``archive_workspace`` — mirrors :class:`ArchiveResult`."""

    relative_path: str
    size_bytes: int
    hash_sha256: str
    file_count: int
    created_at: datetime


async def archive_workspace_handler(
    args: ArchiveWorkspaceInput,
    ctx: ToolContext,
) -> ArchiveWorkspaceOutput:
    result: ArchiveResult = build_archive(
        session_id=ctx.session_id,
        workspace_manager=ctx.workspace_manager,
    )
    now = datetime.now(UTC)
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={
            "artifact_type": ArtifactType.ARCHIVE.value,
            "path": result.relative_path,
            "size_bytes": result.size_bytes,
            "hash_sha256": result.hash_sha256,
            "file_count": result.file_count,
            "summary": args.summary or None,
        },
        step=ctx.step,
        ts=now,
    )
    return ArchiveWorkspaceOutput(
        relative_path=result.relative_path,
        size_bytes=result.size_bytes,
        hash_sha256=result.hash_sha256,
        file_count=result.file_count,
        created_at=now,
    )


ARCHIVE_WORKSPACE_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="archive_workspace",
        description=(
            "Bundle the load-bearing workspace artifacts (generated/, "
            "working/, outputs/, reports/, events.jsonl, manifest.json) "
            "into a single archive.zip the user can download. Emits "
            "ARTIFACT_GENERATED with artifact_type=archive."
        ),
        input_schema_name="ArchiveWorkspaceInput",
        output_schema_name="ArchiveWorkspaceOutput",
        risk_level=RiskLevel.LOW_WRITE,
        requires_approval=True,
        idempotent=True,
        phases=[ToolPhase.AUTHOR_BUILD, ToolPhase.REPAIR_FIX],
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=ArchiveWorkspaceInput,
    output_schema=ArchiveWorkspaceOutput,
    handler=archive_workspace_handler,
)


__all__ = [
    "ARCHIVE_WORKSPACE_TOOL",
    "ArchiveWorkspaceInput",
    "ArchiveWorkspaceOutput",
]
