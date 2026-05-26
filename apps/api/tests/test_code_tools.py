"""Unit tests for ``write_file`` and ``apply_patch`` (Build Prompt 5b).

The two code-edit write tools are the executor's primary mechanism for
landing the model's generated code. Tests cover:

  * Path discipline (INV-5) via :meth:`WorkspaceManager.resolve_in`.
  * Overwrite refusal by default; opt-in via ``overwrite=True``.
  * File-count budget enforcement.
  * Event emission (``FILE_WRITTEN`` / ``PATCH_APPLIED``).
  * Hash + size reporting in the typed output.
  * Patch failure modes (malformed diff, missing target, hunk reject).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentforge.persistence.workspace import WorkspaceError
from agentforge.schemas import EventKind, RiskLevel
from agentforge.tools.code_tools import (
    APPLY_PATCH_TOOL,
    WRITE_FILE_TOOL,
    ApplyPatchInput,
    PatchApplicationError,
    WriteFileInput,
    apply_patch_handler,
    write_file_handler,
)


def _ws(ctx: Any) -> Path:
    return ctx.workspace_manager.get(ctx.session_id)


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


async def test_write_file_creates_new_file(tool_ctx: Any) -> None:
    out = await write_file_handler(
        WriteFileInput(path="generated/hello.py", content="print('hi')\n"),
        tool_ctx,
    )
    assert out.path == "generated/hello.py"
    assert out.overwritten is False
    assert out.size_bytes == len(b"print('hi')\n")

    written = _ws(tool_ctx) / "generated" / "hello.py"
    assert written.read_text() == "print('hi')\n"


async def test_write_file_refuses_overwrite_by_default(tool_ctx: Any) -> None:
    target = _ws(tool_ctx) / "generated" / "existing.py"
    target.write_text("preexisting")

    with pytest.raises(WorkspaceError, match="refusing to overwrite"):
        await write_file_handler(
            WriteFileInput(path="generated/existing.py", content="new"),
            tool_ctx,
        )
    # The file is unchanged.
    assert target.read_text() == "preexisting"


async def test_write_file_overwrites_when_opted_in(tool_ctx: Any) -> None:
    target = _ws(tool_ctx) / "generated" / "existing.py"
    target.write_text("old")

    out = await write_file_handler(
        WriteFileInput(
            path="generated/existing.py", content="new", overwrite=True
        ),
        tool_ctx,
    )
    assert out.overwritten is True
    assert target.read_text() == "new"


async def test_write_file_rejects_path_traversal(tool_ctx: Any) -> None:
    with pytest.raises(WorkspaceError):
        await write_file_handler(
            WriteFileInput(path="../escape.py", content="x"), tool_ctx
        )


async def test_write_file_rejects_absolute_path(tool_ctx: Any) -> None:
    with pytest.raises(WorkspaceError):
        await write_file_handler(
            WriteFileInput(path="/tmp/escape.py", content="x"), tool_ctx
        )


async def test_write_file_enforces_generated_file_count_budget(
    tool_ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tighten the budget to 2 so we can exercise the cap.
    monkeypatch.setattr(tool_ctx.settings, "budget_generated_file_count", 2)
    await write_file_handler(
        WriteFileInput(path="generated/a.py", content="a"), tool_ctx
    )
    await write_file_handler(
        WriteFileInput(path="generated/b.py", content="b"), tool_ctx
    )

    with pytest.raises(WorkspaceError, match="budget exhausted"):
        await write_file_handler(
            WriteFileInput(path="generated/c.py", content="c"), tool_ctx
        )

    # Overwrites do NOT count against the cap.
    out = await write_file_handler(
        WriteFileInput(path="generated/a.py", content="a2", overwrite=True),
        tool_ctx,
    )
    assert out.overwritten is True


async def test_write_file_emits_file_written_event(tool_ctx: Any) -> None:
    await write_file_handler(
        WriteFileInput(path="generated/x.py", content="y"), tool_ctx
    )
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    file_written = [e for e in events if e.kind == EventKind.FILE_WRITTEN]
    assert len(file_written) == 1
    payload = file_written[0].payload
    assert payload["path"] == "generated/x.py"
    assert payload["overwritten"] is False
    assert len(payload["hash_sha256"]) == 64


def test_write_file_definition_requires_approval() -> None:
    """INV-4: write tools default requires_approval=True with no ADR override."""
    defn = WRITE_FILE_TOOL.definition
    assert defn.requires_approval is True
    assert defn.risk_level == RiskLevel.LOW_WRITE
    assert defn.adr_override is None


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


_INITIAL_CODE = """def add(a, b):
    return a - b  # BUG
"""

_FIX_DIFF = """--- a/generated/calc.py
+++ b/generated/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b  # BUG
+    return a + b  # fixed
"""


async def test_apply_patch_applies_unified_diff(tool_ctx: Any) -> None:
    target = _ws(tool_ctx) / "generated" / "calc.py"
    target.write_text(_INITIAL_CODE)

    out = await apply_patch_handler(
        ApplyPatchInput(file="generated/calc.py", unified_diff=_FIX_DIFF),
        tool_ctx,
    )
    assert out.file == "generated/calc.py"
    assert out.hunks_applied >= 1
    assert target.read_text() == _INITIAL_CODE.replace(
        "    return a - b  # BUG\n",
        "    return a + b  # fixed\n",
    )


async def test_apply_patch_emits_patch_applied_event(tool_ctx: Any) -> None:
    (_ws(tool_ctx) / "generated" / "calc.py").write_text(_INITIAL_CODE)
    await apply_patch_handler(
        ApplyPatchInput(file="generated/calc.py", unified_diff=_FIX_DIFF),
        tool_ctx,
    )
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    applied = [e for e in events if e.kind == EventKind.PATCH_APPLIED]
    assert len(applied) == 1
    assert applied[0].payload["file"] == "generated/calc.py"


async def test_apply_patch_raises_when_target_missing(tool_ctx: Any) -> None:
    with pytest.raises(FileNotFoundError, match="target file"):
        await apply_patch_handler(
            ApplyPatchInput(
                file="generated/nope.py", unified_diff=_FIX_DIFF
            ),
            tool_ctx,
        )


async def test_apply_patch_fails_on_malformed_diff(tool_ctx: Any) -> None:
    (_ws(tool_ctx) / "generated" / "calc.py").write_text(_INITIAL_CODE)
    with pytest.raises(PatchApplicationError):
        await apply_patch_handler(
            ApplyPatchInput(
                file="generated/calc.py",
                unified_diff="this is not a diff at all\n",
            ),
            tool_ctx,
        )


async def test_apply_patch_fails_on_context_mismatch(tool_ctx: Any) -> None:
    """A diff whose context doesn't match the file → patch rejects."""
    (_ws(tool_ctx) / "generated" / "calc.py").write_text(
        "completely different content\n"
    )
    with pytest.raises(PatchApplicationError):
        await apply_patch_handler(
            ApplyPatchInput(file="generated/calc.py", unified_diff=_FIX_DIFF),
            tool_ctx,
        )


def test_apply_patch_definition_requires_approval() -> None:
    defn = APPLY_PATCH_TOOL.definition
    assert defn.requires_approval is True
    assert defn.risk_level == RiskLevel.LOW_WRITE
    assert defn.adr_override is None
