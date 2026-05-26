"""Unit tests for ``list_workspace`` and ``inspect_file`` (Build Prompt 4).

Covers the two reads that every workflow phase needs:

  * Happy-path listing returns workspace-relative paths in stable order.
  * ``max_depth`` and ``max_entries`` are honoured; truncation is
    reported, not silent.
  * Path discipline (INV-5) is enforced via
    :meth:`WorkspaceManager.resolve_in` — absolute paths and ``..``
    escapes raise :class:`WorkspaceError`.
  * ``inspect_file`` returns decoded UTF-8 for text, base64 for binary
    (heuristic: failed UTF-8 decode), and reports ``truncated`` when the
    file is larger than ``max_bytes``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from agentforge.persistence.workspace import WorkspaceError
from agentforge.tools.workspace_tools import (
    InspectFileInput,
    ListWorkspaceInput,
    inspect_file_handler,
    list_workspace_handler,
)


def _ws(tool_ctx: Any) -> Path:
    return tool_ctx.workspace_manager.get(tool_ctx.session_id)


# ---------------------------------------------------------------------------
# list_workspace
# ---------------------------------------------------------------------------


async def test_list_workspace_lists_initial_layout(tool_ctx: Any) -> None:
    out = await list_workspace_handler(ListWorkspaceInput(path="."), tool_ctx)
    names = {e.relative_path for e in out.entries}
    # Subdirs created by WorkspaceManager.allocate.
    assert "uploads" in names
    assert "generated" in names
    assert "outputs" in names
    assert "outputs/_logs" in names
    # events.jsonl + manifest.json are also there at the root.
    assert "events.jsonl" in names
    assert "manifest.json" in names
    assert out.truncated is False


async def test_list_workspace_returns_only_files_under_target(tool_ctx: Any) -> None:
    ws = _ws(tool_ctx)
    (ws / "uploads" / "a.csv").write_text("a,b\n1,2\n")
    (ws / "uploads" / "b.csv").write_text("c,d\n3,4\n")

    out = await list_workspace_handler(
        ListWorkspaceInput(path="uploads", max_depth=1), tool_ctx
    )
    paths = sorted(e.relative_path for e in out.entries)
    assert paths == ["uploads/a.csv", "uploads/b.csv"]
    for e in out.entries:
        assert e.is_dir is False
        assert e.size_bytes is not None and e.size_bytes > 0


async def test_list_workspace_respects_max_depth(tool_ctx: Any) -> None:
    ws = _ws(tool_ctx)
    (ws / "generated" / "subdir").mkdir(parents=True)
    (ws / "generated" / "subdir" / "deep.txt").write_text("deep")

    shallow = await list_workspace_handler(
        ListWorkspaceInput(path="generated", max_depth=1), tool_ctx
    )
    paths = {e.relative_path for e in shallow.entries}
    assert "generated/subdir" in paths
    # max_depth=1 means children of 'generated' only; not deeper.
    assert "generated/subdir/deep.txt" not in paths

    deep = await list_workspace_handler(
        ListWorkspaceInput(path="generated", max_depth=3), tool_ctx
    )
    deep_paths = {e.relative_path for e in deep.entries}
    assert "generated/subdir/deep.txt" in deep_paths


async def test_list_workspace_truncates_and_flags(tool_ctx: Any) -> None:
    ws = _ws(tool_ctx)
    for i in range(20):
        (ws / "uploads" / f"f_{i:02d}.csv").write_text("x")

    out = await list_workspace_handler(
        ListWorkspaceInput(path="uploads", max_entries=5), tool_ctx
    )
    assert len(out.entries) == 5
    assert out.truncated is True


async def test_list_workspace_rejects_absolute_path(tool_ctx: Any) -> None:
    with pytest.raises(WorkspaceError, match="absolute"):
        await list_workspace_handler(ListWorkspaceInput(path="/etc"), tool_ctx)


async def test_list_workspace_rejects_parent_escape(tool_ctx: Any) -> None:
    with pytest.raises(WorkspaceError, match="escapes workspace"):
        await list_workspace_handler(ListWorkspaceInput(path="../.."), tool_ctx)


# ---------------------------------------------------------------------------
# inspect_file
# ---------------------------------------------------------------------------


async def test_inspect_file_returns_decoded_text(tool_ctx: Any) -> None:
    ws = _ws(tool_ctx)
    body = "txn_id,amount\nT-1,42.00\n"
    (ws / "uploads" / "in.csv").write_text(body)

    out = await inspect_file_handler(InspectFileInput(path="uploads/in.csv"), tool_ctx)
    assert out.is_binary is False
    assert out.content == body
    assert out.truncated is False
    assert out.size_bytes == len(body.encode("utf-8"))
    assert out.path == "uploads/in.csv"


async def test_inspect_file_reports_binary_as_base64(tool_ctx: Any) -> None:
    ws = _ws(tool_ctx)
    payload = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03\xff\xfe"
    (ws / "uploads" / "logo.png").write_bytes(payload)

    out = await inspect_file_handler(
        InspectFileInput(path="uploads/logo.png"), tool_ctx
    )
    assert out.is_binary is True
    assert base64.b64decode(out.content.encode("ascii")) == payload


async def test_inspect_file_truncates_large_files(tool_ctx: Any) -> None:
    ws = _ws(tool_ctx)
    body = "a" * 1024
    (ws / "uploads" / "big.txt").write_text(body)

    out = await inspect_file_handler(
        InspectFileInput(path="uploads/big.txt", max_bytes=100), tool_ctx
    )
    assert out.truncated is True
    assert out.size_bytes == 1024
    assert out.content == "a" * 100


async def test_inspect_file_rejects_absolute_path(tool_ctx: Any) -> None:
    with pytest.raises(WorkspaceError):
        await inspect_file_handler(
            InspectFileInput(path="/etc/passwd"), tool_ctx
        )


async def test_inspect_file_rejects_parent_escape(tool_ctx: Any) -> None:
    with pytest.raises(WorkspaceError):
        await inspect_file_handler(
            InspectFileInput(path="../escape.txt"), tool_ctx
        )


async def test_inspect_file_raises_filenotfound_on_missing(tool_ctx: Any) -> None:
    with pytest.raises(FileNotFoundError):
        await inspect_file_handler(
            InspectFileInput(path="uploads/does_not_exist.csv"), tool_ctx
        )
