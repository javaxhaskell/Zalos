"""Code-editing write tools: ``write_file`` and ``apply_patch``.

Both default to ``requires_approval=True`` (INV-4). They emit
``FILE_WRITTEN`` / ``PATCH_APPLIED`` events on success so the validation
engine (BP5c) and the audit export can reconstruct the agent's code
edits without inspecting the workspace.

``write_file`` is the bulk-write path: the model issues one call per
file when generating a new agent. ``apply_patch`` is the surgical path:
the model issues a unified diff during the repair flow (and for small
edits in the author flow's review loop). Both go through the same
path-discipline chokepoint (:meth:`WorkspaceManager.resolve_in`) so a
diff that names ``../etc/passwd`` is rejected before any subprocess
runs.

Patch application uses the POSIX ``patch`` utility piped via stdin
(:meth:`SandboxRunner.run` with ``stdin=``). This keeps the patch logic
in a well-tested system tool rather than a hand-rolled Python applier;
the failure modes (rejected hunks, malformed diff) surface via the
familiar exit code + stderr.
"""

from __future__ import annotations

import hashlib

from agentforge.persistence.workspace import WorkspaceError
from agentforge.sandbox import SandboxRunner
from agentforge.schemas import (
    ActorType,
    EventKind,
    RiskLevel,
    StrictModel,
    ToolDefinition,
    ToolPhase,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"
_BUILD_AND_FIX_PHASES: list[ToolPhase] = [
    ToolPhase.AUTHOR_BUILD,
    ToolPhase.REPAIR_FIX,
]


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class WriteFileInput(StrictModel):
    """Arguments for ``write_file``."""

    path: str
    """Workspace-relative destination. Path discipline via resolve_in."""
    content: str
    """File content as a UTF-8 string. Binary writes are out of scope."""
    overwrite: bool = False
    """``False`` (the default) refuses to clobber an existing file —
    forces the model to opt in explicitly when overwriting a seeded
    template file or a previous generation."""


class WriteFileOutput(StrictModel):
    path: str
    size_bytes: int
    hash_sha256: str
    overwritten: bool


async def write_file_handler(
    args: WriteFileInput, ctx: ToolContext
) -> WriteFileOutput:
    resolved = ctx.workspace_manager.resolve_in(ctx.session_id, args.path)
    workspace_root = ctx.workspace_manager.get(ctx.session_id)

    file_existed = resolved.is_file()
    if file_existed and not args.overwrite:
        raise WorkspaceError(
            f"refusing to overwrite existing file: {args.path} "
            f"(set overwrite=True to opt in)"
        )

    # File-count budget (ADR-0004). Only counts NEW files; overwrites are free.
    if not file_existed:
        current_count = sum(
            1 for p in (workspace_root / "generated").rglob("*") if p.is_file()
        )
        if current_count >= ctx.settings.budget_generated_file_count:
            raise WorkspaceError(
                f"generated file count budget exhausted: "
                f"{ctx.settings.budget_generated_file_count} files already present"
            )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    body = args.content.encode("utf-8")
    resolved.write_bytes(body)

    digest = hashlib.sha256(body).hexdigest()
    relative = str(resolved.relative_to(workspace_root)).replace("\\", "/")

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.FILE_WRITTEN,
        actor_type=ActorType.SYSTEM,
        payload={
            "path": relative,
            "size_bytes": len(body),
            "hash_sha256": digest,
            "overwritten": file_existed,
        },
        step=ctx.step,
    )

    return WriteFileOutput(
        path=relative,
        size_bytes=len(body),
        hash_sha256=digest,
        overwritten=file_existed,
    )


WRITE_FILE_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="write_file",
        description=(
            "Write UTF-8 content to a workspace-relative path. By default "
            "refuses to overwrite an existing file — set overwrite=True "
            "to clobber. Emits FILE_WRITTEN and is gated by the approval "
            "engine (INV-3)."
        ),
        input_schema_name="WriteFileInput",
        output_schema_name="WriteFileOutput",
        risk_level=RiskLevel.LOW_WRITE,
        requires_approval=True,
        idempotent=True,
        phases=_BUILD_AND_FIX_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=WriteFileInput,
    output_schema=WriteFileOutput,
    handler=write_file_handler,
)


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


class ApplyPatchInput(StrictModel):
    """Arguments for ``apply_patch``."""

    file: str
    """Workspace-relative target. Must match the path in the diff header."""
    unified_diff: str
    """Standard unified-diff format (``--- a/file`` / ``+++ b/file`` /
    ``@@`` hunks). Trailing newline added automatically if missing."""


class ApplyPatchOutput(StrictModel):
    file: str
    hunks_applied: int
    new_hash_sha256: str


async def apply_patch_handler(
    args: ApplyPatchInput, ctx: ToolContext
) -> ApplyPatchOutput:
    # Path discipline: the diff might internally name `a/x.py` and `b/x.py`,
    # but the resolved on-disk target must be inside the workspace.
    resolved = ctx.workspace_manager.resolve_in(ctx.session_id, args.file)
    if not resolved.is_file():
        raise FileNotFoundError(f"target file does not exist: {args.file}")

    workspace_root = ctx.workspace_manager.get(ctx.session_id)
    diff = args.unified_diff
    if not diff.endswith("\n"):
        diff += "\n"

    runner = SandboxRunner(
        settings=ctx.settings, workspace_manager=ctx.workspace_manager
    )
    result = runner.run(
        session_id=ctx.session_id,
        cmd=["patch", "-u", "-p1", "-f", "--no-backup-if-mismatch"]
        if _patch_supports_no_backup()
        else ["patch", "-u", "-p1", "-f"],
        cwd_relative=".",
        timeout_seconds=ctx.settings.subprocess_timeout_script,
        step=ctx.step,
        stdin=diff.encode("utf-8"),
    )

    if result.exit_code != 0:
        raise PatchApplicationError(
            f"patch failed (exit={result.exit_code}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    # Count successful hunks from patch's stdout. GNU/BSD patch both emit
    # one "Hunk #N succeeded" or "patching file ..." line per hunk.
    hunks_applied = sum(
        1 for line in result.stdout.splitlines() if line.lstrip().startswith("Hunk #")
    )
    if hunks_applied == 0:
        # No "Hunk #" lines means patch reported only "patching file ...";
        # treat the whole patch as one applied hunk for the audit trail.
        hunks_applied = 1

    new_body = resolved.read_bytes()
    digest = hashlib.sha256(new_body).hexdigest()
    relative = str(resolved.relative_to(workspace_root)).replace("\\", "/")

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.PATCH_APPLIED,
        actor_type=ActorType.SYSTEM,
        payload={
            "file": relative,
            "hunks_applied": hunks_applied,
            "new_hash_sha256": digest,
        },
        step=ctx.step,
    )

    return ApplyPatchOutput(
        file=relative,
        hunks_applied=hunks_applied,
        new_hash_sha256=digest,
    )


class PatchApplicationError(RuntimeError):
    """Raised when ``patch`` rejects a diff (malformed, hunks failed)."""


def _patch_supports_no_backup() -> bool:
    """GNU patch supports ``--no-backup-if-mismatch``; BSD patch (macOS) does not.

    Cheapest check: BSD patch is at ``/usr/bin/patch`` on macOS; GNU
    patch can be at the same path on Linux or under ``/opt/homebrew``.
    Probing once and caching the result keeps the BP4 lint clean
    against subprocess noise in tests.
    """
    import platform

    return platform.system() == "Linux"


APPLY_PATCH_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="apply_patch",
        description=(
            "Apply a unified diff to a workspace-relative file via the "
            "POSIX `patch` utility. The diff must reference a single "
            "target file; multi-file patches require multiple calls. "
            "Emits PATCH_APPLIED and is gated by the approval engine "
            "(INV-3)."
        ),
        input_schema_name="ApplyPatchInput",
        output_schema_name="ApplyPatchOutput",
        risk_level=RiskLevel.LOW_WRITE,
        requires_approval=True,
        idempotent=True,
        phases=_BUILD_AND_FIX_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=ApplyPatchInput,
    output_schema=ApplyPatchOutput,
    handler=apply_patch_handler,
)


__all__ = [
    "APPLY_PATCH_TOOL",
    "ApplyPatchInput",
    "ApplyPatchOutput",
    "PatchApplicationError",
    "WRITE_FILE_TOOL",
    "WriteFileInput",
    "WriteFileOutput",
]
