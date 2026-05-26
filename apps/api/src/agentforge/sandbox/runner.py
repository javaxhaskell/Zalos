"""Bounded subprocess primitive used by execution tools.

The runner is the only place in the backend that spawns child processes.
It enforces four invariants — three of them load-bearing for INV-5
(workspace boundary) and INV-12 (bounded loops):

1. ``cwd`` is pinned inside the session workspace (via
   :meth:`WorkspaceManager.resolve_in`). Path traversal in
   ``cwd_relative`` raises before the child is spawned.
2. A timeout is always set (default from
   :attr:`Settings.subprocess_timeout_script`). On expiry the child is
   killed and the captured prefix is returned with ``timed_out=True``.
3. The environment is minimal — ``PATH``, ``PYTHONPATH``, ``LANG`` plus
   anything in ``env_extra``. The host's secrets (``ANTHROPIC_API_KEY``
   et al.) are not inherited into the sandbox.
4. Each stream is capped at ``subprocess_output_max_bytes`` (1 MiB).
   Truncation is reported in the result; the full bytes are spilled to
   ``outputs/_logs/{step}.log`` so the audit trail is complete.

BP4 ships the primitive only. BP5/6 wire it into ``run_python_script``
and ``run_pytest`` (write tools) once the executor + approval gate
exist. The primitive itself does not enforce approval; that is the
executor's job.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from uuid import UUID

from agentforge.config import Settings
from agentforge.persistence.workspace import WorkspaceManager

_TIMEOUT_EXIT_CODE = -9
"""Sentinel exit code for SIGKILL-on-timeout. Matches POSIX -SIGKILL."""


@dataclass
class SubprocessResult:
    """Outcome of a single :meth:`SandboxRunner.run` invocation."""

    exit_code: int
    """``0`` on success; ``-9`` when killed by the timeout guard."""
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    overflow_log_path: str | None
    """Workspace-relative path to ``outputs/_logs/{step}.log`` if either
    stream overflowed; ``None`` otherwise."""
    latency_ms: int
    timed_out: bool


class SandboxRunner:
    """Run subprocesses inside a session workspace under bounded conditions."""

    def __init__(
        self,
        *,
        settings: Settings,
        workspace_manager: WorkspaceManager,
    ) -> None:
        self.settings = settings
        self.wm = workspace_manager

    def run(
        self,
        *,
        session_id: UUID,
        cmd: list[str],
        cwd_relative: str = ".",
        timeout_seconds: int | None = None,
        env_extra: dict[str, str] | None = None,
        step: int = 0,
        stdin: bytes | None = None,
    ) -> SubprocessResult:
        """Execute ``cmd`` under the session sandbox.

        ``cwd_relative`` resolves through :meth:`WorkspaceManager.resolve_in`
        so absolute paths and ``..`` segments raise before spawn. The
        environment is built from scratch — see module docstring. The
        ``step`` index is used only for the overflow-log filename.

        ``stdin`` is fed to the child process; ``apply_patch`` uses this to
        pipe a unified diff to ``patch -u -p1``. Pass ``None`` to keep
        the child's stdin closed (the default — equivalent to the BP4
        behaviour for read-only commands like ``pwd`` and ``echo``).
        """
        cwd = self.wm.resolve_in(session_id, cwd_relative)
        if not cwd.is_dir():
            from agentforge.persistence.workspace import WorkspaceError

            raise WorkspaceError(f"cwd is not a directory: {cwd_relative}")

        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.subprocess_timeout_script
        )

        env = self._build_env(env_extra)
        max_bytes = self.settings.subprocess_output_max_bytes

        start = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(  # noqa: S603 — cmd is caller-supplied list
                cmd,
                cwd=str(cwd),
                env=env,
                input=stdin,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            stdout_bytes = completed.stdout or b""
            stderr_bytes = completed.stderr or b""
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout_bytes = exc.stdout or b""
            stderr_bytes = exc.stderr or b""
            exit_code = _TIMEOUT_EXIT_CODE
            timed_out = True
        latency_ms = int((time.monotonic() - start) * 1000)

        stdout_str, stdout_truncated = _truncate(stdout_bytes, max_bytes)
        stderr_str, stderr_truncated = _truncate(stderr_bytes, max_bytes)

        overflow_log_path: str | None = None
        if stdout_truncated or stderr_truncated:
            overflow_log_path = _write_overflow_log(
                session_id=session_id,
                workspace_manager=self.wm,
                step=step,
                stdout=stdout_bytes,
                stderr=stderr_bytes,
            )

        return SubprocessResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            overflow_log_path=overflow_log_path,
            latency_ms=latency_ms,
            timed_out=timed_out,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_env(env_extra: dict[str, str] | None) -> dict[str, str]:
        """Return a minimal environment for the child process.

        The host's ``ANTHROPIC_API_KEY`` and other secrets are deliberately
        not inherited. Generated agents have no business calling the LLM
        from inside the sandbox.
        """
        import os

        env: dict[str, str] = {
            "PATH": os.environ.get(
                "PATH", "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            ),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        if env_extra:
            env.update(env_extra)
        return env


def _truncate(data: bytes, max_bytes: int) -> tuple[str, bool]:
    """Decode at most ``max_bytes`` of ``data`` as UTF-8 with replacement.

    Bytes beyond the limit are reported as truncated; the full content
    is written to the overflow log so nothing is lost from the audit.
    """
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace"), False
    head = data[:max_bytes].decode("utf-8", errors="replace")
    return head, True


def _write_overflow_log(
    *,
    session_id: UUID,
    workspace_manager: WorkspaceManager,
    step: int,
    stdout: bytes,
    stderr: bytes,
) -> str:
    """Write full streams to ``outputs/_logs/{step}.log`` and return the relative path."""
    logs_dir = workspace_manager.resolve_in(session_id, "outputs/_logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{step}.log"
    with log_path.open("wb") as f:
        f.write(b"===== STDOUT =====\n")
        f.write(stdout)
        f.write(b"\n===== STDERR =====\n")
        f.write(stderr)
    workspace_root = workspace_manager.get(session_id)
    return str(log_path.relative_to(workspace_root)).replace("\\", "/")
