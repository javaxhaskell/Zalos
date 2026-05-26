"""Unit tests for the bounded subprocess primitive (Build Prompt 4).

Covers the four behaviours the runner exists to guarantee:

  * ``cwd`` is pinned inside the workspace — a ``cwd_relative`` that
    tries to escape raises :class:`WorkspaceError` before the child is
    spawned (INV-5).
  * The child's working directory is the resolved path (not the host's
    cwd) — verified by running ``pwd`` and comparing.
  * Timeouts always fire; SIGKILL produces ``timed_out=True`` and
    ``exit_code=-9`` (INV-12).
  * Each stream is capped at ``subprocess_output_max_bytes``; truncation
    is reported and the full bytes are written to
    ``outputs/_logs/{step}.log``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agentforge.config import Settings
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceError, WorkspaceManager
from agentforge.sandbox import SandboxRunner
from agentforge.schemas import Workflow
from agentforge.tools import ToolContext


@pytest.fixture()
def runner_setup(workspaces_root: Path) -> tuple[SandboxRunner, ToolContext]:
    """Build a runner and a fresh session workspace.

    Returns a pair ``(runner, ctx)`` so each test can choose how to use
    them (most tests want the session_id from the context).
    """
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    settings = Settings(
        subprocess_timeout_script=10,
        subprocess_output_max_bytes=4096,  # tighter cap → easier truncation test
    )
    runner = SandboxRunner(settings=settings, workspace_manager=wm)
    ctx = ToolContext(
        session_id=sid,
        step=0,
        workspace_manager=wm,
        event_log=EventLog(wm),
        settings=settings,
    )
    return runner, ctx


def test_sandbox_runner_runs_simple_command(runner_setup: Any) -> None:
    runner, ctx = runner_setup
    result = runner.run(
        session_id=ctx.session_id,
        cmd=["/bin/echo", "hello world"],
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello world"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.stdout_truncated is False
    assert result.stderr_truncated is False
    assert result.overflow_log_path is None
    assert result.latency_ms >= 0


def test_sandbox_runner_pins_cwd_to_workspace(runner_setup: Any) -> None:
    """The child sees the resolved workspace path as its cwd, not the host cwd."""
    runner, ctx = runner_setup
    workspace = ctx.workspace_manager.get(ctx.session_id)

    result = runner.run(
        session_id=ctx.session_id,
        cmd=["/bin/sh", "-c", "pwd"],
    )
    assert result.exit_code == 0
    # /bin/sh -c pwd may print the real path; resolve both for comparison
    # so symlinks (e.g., /tmp → /private/tmp on macOS) don't break this.
    reported = Path(result.stdout.strip()).resolve()
    assert reported == workspace.resolve()


def test_sandbox_runner_pins_cwd_to_subdirectory(runner_setup: Any) -> None:
    runner, ctx = runner_setup
    workspace = ctx.workspace_manager.get(ctx.session_id)

    result = runner.run(
        session_id=ctx.session_id,
        cmd=["/bin/sh", "-c", "pwd"],
        cwd_relative="uploads",
    )
    assert result.exit_code == 0
    reported = Path(result.stdout.strip()).resolve()
    assert reported == (workspace / "uploads").resolve()


def test_sandbox_runner_rejects_parent_escape(runner_setup: Any) -> None:
    runner, ctx = runner_setup
    with pytest.raises(WorkspaceError):
        runner.run(
            session_id=ctx.session_id,
            cmd=["/bin/echo", "ignored"],
            cwd_relative="../escape",
        )


def test_sandbox_runner_rejects_absolute_cwd(runner_setup: Any) -> None:
    runner, ctx = runner_setup
    with pytest.raises(WorkspaceError):
        runner.run(
            session_id=ctx.session_id,
            cmd=["/bin/echo", "ignored"],
            cwd_relative="/etc",
        )


def test_sandbox_runner_enforces_timeout(runner_setup: Any) -> None:
    runner, ctx = runner_setup
    result = runner.run(
        session_id=ctx.session_id,
        cmd=["/bin/sh", "-c", "sleep 3"],
        timeout_seconds=1,
    )
    assert result.timed_out is True
    assert result.exit_code == -9
    # On timeout the runner does NOT raise — it returns a structured
    # result so the executor (BP5) can record the EXECUTION_FAILED event.


def test_sandbox_runner_truncates_oversized_stdout_and_writes_overflow(
    runner_setup: Any,
) -> None:
    runner, ctx = runner_setup
    # subprocess_output_max_bytes was set to 4096 in the fixture; emit ~16 KiB
    # via Python so the script is portable across BSD/GNU echo.
    result = runner.run(
        session_id=ctx.session_id,
        cmd=[sys.executable, "-c", "print('x' * 16000)"],
        step=7,
    )
    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 4096
    assert result.overflow_log_path == "outputs/_logs/7.log"
    workspace = ctx.workspace_manager.get(ctx.session_id)
    overflow = workspace / "outputs/_logs/7.log"
    assert overflow.is_file()
    body = overflow.read_text()
    assert "===== STDOUT =====" in body
    # The overflow log carries the full pre-truncation payload.
    assert body.count("x") >= 16000


def test_sandbox_runner_does_not_inherit_host_secrets(runner_setup: Any) -> None:
    """ANTHROPIC_API_KEY in the host env must NOT leak into the child."""
    import os

    runner, ctx = runner_setup
    os.environ["ANTHROPIC_API_KEY"] = "should-not-leak"
    try:
        result = runner.run(
            session_id=ctx.session_id,
            cmd=[sys.executable, "-c", "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'absent'))"],
        )
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    assert result.stdout.strip() == "absent"


def test_sandbox_runner_passes_env_extra(runner_setup: Any) -> None:
    runner, ctx = runner_setup
    result = runner.run(
        session_id=ctx.session_id,
        cmd=[sys.executable, "-c", "import os; print(os.environ['AGENTFORGE_TEST_VAR'])"],
        env_extra={"AGENTFORGE_TEST_VAR": "carried"},
    )
    assert result.stdout.strip() == "carried"
