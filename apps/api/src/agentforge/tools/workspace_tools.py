"""Read tools for inspecting the per-session workspace.

Both tools are ``risk_level=READ``, ``requires_approval=False``, and
exposed in every workflow phase. They share two invariants:

  * Every path passes through :meth:`WorkspaceManager.resolve_in`, which
    rejects absolute paths and ``..`` escapes (INV-5).
  * Entry counts and byte counts are capped; truncation is reported in
    the typed output so the model can decide whether to fetch more.
"""

from __future__ import annotations

import base64
from collections import deque
from pathlib import Path

from agentforge.schemas import RiskLevel, StrictModel, ToolDefinition, ToolPhase
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"
_ALL_PHASES: list[ToolPhase] = [
    ToolPhase.AUTHOR_INFO,
    ToolPhase.AUTHOR_BUILD,
    ToolPhase.REPAIR_INFO,
    ToolPhase.REPAIR_FIX,
]


# ---------------------------------------------------------------------------
# list_workspace
# ---------------------------------------------------------------------------


class ListWorkspaceInput(StrictModel):
    """Arguments for ``list_workspace``.

    ``path`` is workspace-relative; ``"."`` lists the workspace root.
    ``max_depth`` caps recursion (``1`` lists only the immediate children
    of ``path``). ``max_entries`` caps the total result length; on
    truncation the output flags ``truncated=True`` so the model can
    re-call with a narrower ``path``.
    """

    path: str = "."
    max_depth: int = 3
    max_entries: int = 500


class WorkspaceEntry(StrictModel):
    """One file or directory inside the workspace."""

    relative_path: str
    """Path relative to the workspace root (forward-slash, no leading ``/``)."""
    is_dir: bool
    size_bytes: int | None = None
    """``None`` for directories."""


class ListWorkspaceOutput(StrictModel):
    root: str
    """The resolved root for this listing, workspace-relative."""
    entries: list[WorkspaceEntry]
    truncated: bool


async def list_workspace_handler(
    args: ListWorkspaceInput, ctx: ToolContext
) -> ListWorkspaceOutput:
    """Walk ``args.path`` up to ``max_depth``, returning at most ``max_entries``."""
    workspace_root = ctx.workspace_manager.get(ctx.session_id)
    base = ctx.workspace_manager.resolve_in(ctx.session_id, args.path)
    if not base.exists():
        return ListWorkspaceOutput(
            root=_relative_str(base, workspace_root),
            entries=[],
            truncated=False,
        )

    entries: list[WorkspaceEntry] = []
    truncated = False

    if base.is_file():
        entries.append(_make_entry(base, workspace_root))
        return ListWorkspaceOutput(
            root=_relative_str(base, workspace_root),
            entries=entries,
            truncated=False,
        )

    # Breadth-first so the depth bound is uniform across siblings.
    queue: deque[tuple[Path, int]] = deque([(base, 0)])
    while queue:
        current, depth = queue.popleft()
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name)
        except PermissionError:
            continue
        for child in children:
            if len(entries) >= args.max_entries:
                truncated = True
                break
            entries.append(_make_entry(child, workspace_root))
            if child.is_dir() and depth + 1 < args.max_depth:
                queue.append((child, depth + 1))
        if truncated:
            break

    return ListWorkspaceOutput(
        root=_relative_str(base, workspace_root),
        entries=entries,
        truncated=truncated,
    )


def _make_entry(child: Path, workspace_root: Path) -> WorkspaceEntry:
    is_dir = child.is_dir()
    return WorkspaceEntry(
        relative_path=_relative_str(child, workspace_root),
        is_dir=is_dir,
        size_bytes=None if is_dir else child.stat().st_size,
    )


def _relative_str(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` with forward slashes.

    The workspace root itself is represented as ``"."`` rather than the
    empty string so the model always sees a non-empty token.
    """
    if path == root:
        return "."
    return str(path.relative_to(root)).replace("\\", "/")


LIST_WORKSPACE_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="list_workspace",
        description=(
            "List files and directories within the session workspace, "
            "up to max_depth. Paths are workspace-relative; use '.' to "
            "list the workspace root."
        ),
        input_schema_name="ListWorkspaceInput",
        output_schema_name="ListWorkspaceOutput",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_ALL_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=ListWorkspaceInput,
    output_schema=ListWorkspaceOutput,
    handler=list_workspace_handler,
)


# ---------------------------------------------------------------------------
# inspect_file
# ---------------------------------------------------------------------------


class InspectFileInput(StrictModel):
    """Arguments for ``inspect_file``.

    ``max_bytes`` caps how much of a large file is returned. ``64 KiB`` is
    enough for CSV headers + a few sample rows, which is what the
    repair-flow uses for plain-English summaries.
    """

    path: str
    max_bytes: int = 65_536


class InspectFileOutput(StrictModel):
    path: str
    size_bytes: int
    """The on-disk size of the file (may exceed ``max_bytes``)."""
    truncated: bool
    """True iff ``size_bytes > max_bytes``."""
    is_binary: bool
    """True iff the prefix did not decode as UTF-8."""
    content: str
    """Decoded text when ``is_binary=False``; base64-encoded bytes otherwise."""


async def inspect_file_handler(
    args: InspectFileInput, ctx: ToolContext
) -> InspectFileOutput:
    """Read up to ``max_bytes`` from ``args.path``; base64 if not UTF-8."""
    workspace_root = ctx.workspace_manager.get(ctx.session_id)
    resolved = ctx.workspace_manager.resolve_in(ctx.session_id, args.path)
    if not resolved.is_file():
        # Distinct from path discipline (handled by resolve_in): the
        # path is well-formed but does not name a file.
        raise FileNotFoundError(f"not a file: {args.path}")

    size_bytes = resolved.stat().st_size
    with resolved.open("rb") as f:
        prefix = f.read(args.max_bytes)
    truncated = size_bytes > args.max_bytes

    try:
        text = prefix.decode("utf-8")
        is_binary = False
        content = text
    except UnicodeDecodeError:
        is_binary = True
        content = base64.b64encode(prefix).decode("ascii")

    return InspectFileOutput(
        path=_relative_str(resolved, workspace_root),
        size_bytes=size_bytes,
        truncated=truncated,
        is_binary=is_binary,
        content=content,
    )


INSPECT_FILE_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="inspect_file",
        description=(
            "Read up to max_bytes from a workspace-relative file. Returns "
            "decoded UTF-8 text when possible, base64-encoded bytes "
            "otherwise. truncated=True when the file is larger than max_bytes."
        ),
        input_schema_name="InspectFileInput",
        output_schema_name="InspectFileOutput",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_ALL_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=InspectFileInput,
    output_schema=InspectFileOutput,
    handler=inspect_file_handler,
)
