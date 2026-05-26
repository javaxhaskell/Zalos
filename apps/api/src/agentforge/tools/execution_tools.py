"""Execution write tools: ``run_python_script`` and ``run_pytest``.

Both are ``risk_level=HIGH_WRITE`` and ``requires_approval=True`` (INV-4).
They wrap :class:`SandboxRunner` and translate its raw
:class:`SubprocessResult` into the canonical
:class:`ExecutionObservation` / :class:`TestResults` schemas so
downstream phases see one consistent shape.

The first-time-per-session consent semantic described in ADR-0006
("one approval covers all subsequent same-session executions") lives
in the BP5c orchestrator, not here — the agent loop's per-step gate
remains strict; the orchestrator pre-records an ``APPROVAL_GRANTED``
event with a covering scope when the user grants run-consent.
"""

from __future__ import annotations

import re
import sys
from uuid import UUID, uuid4

from agentforge.sandbox import SandboxRunner
from agentforge.schemas import (
    ActorType,
    ErrorCode,
    EventKind,
    ExecutionObservation,
    PerTestResult,
    RiskLevel,
    StrictModel,
    TestResults,
    ToolDefinition,
    ToolPhase,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"

_EXECUTION_PHASES: list[ToolPhase] = [
    ToolPhase.AUTHOR_BUILD,
    ToolPhase.REPAIR_INFO,  # reproduction lives in repair_info
    ToolPhase.REPAIR_FIX,
]


# ---------------------------------------------------------------------------
# run_python_script
# ---------------------------------------------------------------------------


class RunPythonScriptInput(StrictModel):
    """Arguments for ``run_python_script``."""

    script_path: str
    """Workspace-relative path to a ``.py`` file."""
    args: list[str] = []
    """Positional arguments passed after the script path."""
    cwd: str = "."
    """Workspace-relative cwd for the subprocess. Default is workspace root."""
    timeout_seconds: int | None = None
    """Defaults to :attr:`Settings.subprocess_timeout_script`."""


async def run_python_script_handler(
    args: RunPythonScriptInput, ctx: ToolContext
) -> ExecutionObservation:
    invocation_id = uuid4()
    runner = SandboxRunner(
        settings=ctx.settings, workspace_manager=ctx.workspace_manager
    )

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.EXECUTION_STARTED,
        actor_type=ActorType.SYSTEM,
        payload={
            "invocation_id": str(invocation_id),
            "script_path": args.script_path,
            "args": args.args,
        },
        step=ctx.step,
    )

    result = runner.run(
        session_id=ctx.session_id,
        cmd=[sys.executable, args.script_path, *args.args],
        cwd_relative=args.cwd,
        timeout_seconds=args.timeout_seconds,
        step=ctx.step,
    )

    success = result.exit_code == 0 and not result.timed_out
    observation = ExecutionObservation(
        invocation_id=invocation_id,
        success=success,
        exit_code=result.exit_code,
        stdout_excerpt=result.stdout,
        stderr_excerpt=result.stderr,
        files_written=[],
        # BP5c's validation engine will diff-walk the workspace
        # to populate this; the runner doesn't snapshot today.
        latency_ms=result.latency_ms,
        truncated=result.stdout_truncated or result.stderr_truncated,
        overflow_log_path=result.overflow_log_path,
    )

    kind = EventKind.EXECUTION_COMPLETED if success else EventKind.EXECUTION_FAILED
    error_code: ErrorCode | None = None
    if result.timed_out:
        error_code = ErrorCode.COMMAND_TIMEOUT
    elif not success:
        error_code = ErrorCode.GENERATED_CODE_FAILED

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=kind,
        actor_type=ActorType.SYSTEM,
        payload={
            "invocation_id": str(invocation_id),
            "exit_code": result.exit_code,
            "latency_ms": result.latency_ms,
            "timed_out": result.timed_out,
            "truncated": observation.truncated,
            "error_code": error_code.value if error_code else None,
        },
        step=ctx.step,
    )

    return observation


RUN_PYTHON_SCRIPT_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="run_python_script",
        description=(
            "Execute a workspace-relative Python script via the sandbox "
            "runner (cwd-pinned, timeout-bounded, 1 MiB stdout/stderr "
            "caps). Returns an ExecutionObservation. requires_approval=True; "
            "the first run-consent for a session covers subsequent runs "
            "per ADR-0006 (orchestrator records the covering approval)."
        ),
        input_schema_name="RunPythonScriptInput",
        output_schema_name="ExecutionObservation",
        risk_level=RiskLevel.HIGH_WRITE,
        requires_approval=True,
        idempotent=True,
        phases=_EXECUTION_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=RunPythonScriptInput,
    output_schema=ExecutionObservation,
    handler=run_python_script_handler,
)


# ---------------------------------------------------------------------------
# run_pytest
# ---------------------------------------------------------------------------


class RunPytestInput(StrictModel):
    """Arguments for ``run_pytest``."""

    tests_path: str = "tests/"
    """Workspace-relative path to the tests directory or a specific test file."""
    cwd: str = "."
    """Workspace-relative cwd for pytest. Default is workspace root."""
    timeout_seconds: int | None = None
    """Defaults to :attr:`Settings.subprocess_timeout_pytest`."""


# pytest -v line:  tests/test_x.py::test_y PASSED [ 33%]
_PYTEST_LINE_RE = re.compile(
    r"^(?P<path>\S+)::(?P<name>\S+)\s+(?P<status>PASSED|FAILED|SKIPPED|ERROR)"
    r"(?:\s+\[\s*\d+%\])?\s*$"
)
# pytest summary:  === 1 failed, 2 passed, 1 skipped in 0.12s ===
_PYTEST_SUMMARY_RE = re.compile(
    r"=+\s*"
    r"(?:(?P<failed>\d+)\s+failed,?\s*)?"
    r"(?:(?P<passed>\d+)\s+passed,?\s*)?"
    r"(?:(?P<skipped>\d+)\s+skipped,?\s*)?"
    r"(?:(?P<errors>\d+)\s+errors?,?\s*)?"
    r".*?in\s+[\d.]+s.*=+"
)


async def run_pytest_handler(
    args: RunPytestInput, ctx: ToolContext
) -> TestResults:
    invocation_id = uuid4()
    timeout = (
        args.timeout_seconds
        if args.timeout_seconds is not None
        else ctx.settings.subprocess_timeout_pytest
    )
    runner = SandboxRunner(
        settings=ctx.settings, workspace_manager=ctx.workspace_manager
    )

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.TEST_RUN_STARTED,
        actor_type=ActorType.SYSTEM,
        payload={
            "invocation_id": str(invocation_id),
            "tests_path": args.tests_path,
        },
        step=ctx.step,
    )

    result = runner.run(
        session_id=ctx.session_id,
        cmd=[sys.executable, "-m", "pytest", "-v", args.tests_path],
        cwd_relative=args.cwd,
        timeout_seconds=timeout,
        step=ctx.step,
    )

    test_results = _parse_pytest_output(
        invocation_id=invocation_id,
        stdout=result.stdout,
        latency_ms=result.latency_ms,
    )

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.TEST_RUN_COMPLETED,
        actor_type=ActorType.SYSTEM,
        payload={
            "invocation_id": str(invocation_id),
            "passed_count": test_results.passed_count,
            "failed_count": test_results.failed_count,
            "skipped_count": test_results.skipped_count,
            "summary_line": test_results.summary_line,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        step=ctx.step,
    )

    return test_results


def _parse_pytest_output(
    *, invocation_id: UUID, stdout: str, latency_ms: int
) -> TestResults:
    """Parse ``pytest -v`` stdout into canonical :class:`TestResults`."""
    per_test: list[PerTestResult] = []
    summary_line = ""
    for raw in stdout.splitlines():
        line = raw.rstrip()
        m = _PYTEST_LINE_RE.match(line)
        if m:
            test_name = f"{m.group('path')}::{m.group('name')}"
            per_test.append(
                PerTestResult(
                    name=test_name,
                    status=m.group("status").lower(),
                    humanised_name=_humanise(m.group("name")),
                    latency_ms=0,  # pytest -v doesn't break out per-test timings
                )
            )
            continue
        if _PYTEST_SUMMARY_RE.search(line):
            summary_line = line.strip()

    # Summary parse (totals)
    counts = {"failed": 0, "passed": 0, "skipped": 0, "errors": 0}
    if summary_line:
        m = _PYTEST_SUMMARY_RE.search(summary_line)
        if m:
            for key in counts:
                value = m.group(key)
                if value is not None:
                    counts[key] = int(value)

    # If we have per-test lines but the summary didn't parse, fall back
    # to per-test counts so the totals stay consistent.
    if not summary_line and per_test:
        counts["passed"] = sum(1 for t in per_test if t.status == "passed")
        counts["failed"] = sum(1 for t in per_test if t.status == "failed")
        counts["skipped"] = sum(1 for t in per_test if t.status == "skipped")
        counts["errors"] = sum(1 for t in per_test if t.status == "error")
        summary_line = (
            f"{counts['failed']} failed, {counts['passed']} passed "
            f"(parsed from per-test lines)"
        )

    total = sum(counts.values())
    raw_excerpt = stdout[-4096:] if len(stdout) > 4096 else stdout

    return TestResults(
        invocation_id=invocation_id,
        passed_count=counts["passed"],
        failed_count=counts["failed"],
        skipped_count=counts["skipped"],
        error_count=counts["errors"],
        total_count=total,
        summary_line=summary_line or "no tests collected",
        per_test=per_test,
        raw_output_excerpt=raw_excerpt,
    )


def _humanise(test_name: str) -> str:
    """Render ``test_foo_bar_baz`` as ``Foo bar baz`` for finance-user UX."""
    stripped = test_name.removeprefix("test_")
    return stripped.replace("_", " ").capitalize()


RUN_PYTEST_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="run_pytest",
        description=(
            "Run pytest in the session workspace. Returns a TestResults "
            "with per-test status, passed/failed counts, and a summary "
            "line. The runner uses pytest -v and parses stdout — pytest "
            "must be importable inside the subprocess Python."
        ),
        input_schema_name="RunPytestInput",
        output_schema_name="TestResults",
        risk_level=RiskLevel.HIGH_WRITE,
        requires_approval=True,
        idempotent=True,
        phases=_EXECUTION_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=RunPytestInput,
    output_schema=TestResults,
    handler=run_pytest_handler,
)


__all__ = [
    "RUN_PYTEST_TOOL",
    "RUN_PYTHON_SCRIPT_TOOL",
    "RunPytestInput",
    "RunPythonScriptInput",
]
