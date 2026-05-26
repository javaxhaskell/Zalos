"""Unit tests for ``run_python_script`` and ``run_pytest`` (Build Prompt 5b).

The execution tools wrap :class:`SandboxRunner` and translate its raw
:class:`SubprocessResult` into the canonical execution / test schemas.
Tests cover:

  * Successful execution with stdout captured into the typed output.
  * Non-zero exit code → ``success=False`` + ``EXECUTION_FAILED`` event.
  * Timeout → ``timed_out=True`` + ``COMMAND_TIMEOUT`` error code.
  * pytest happy path on the bank_categoriser template seeded into the
    workspace (the same end-to-end shape the agent loop will exercise
    in BP5c).
  * pytest stdout parsing across pass/fail mixes.
  * pytest's "no tests collected" graceful fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentforge.schemas import (
    ErrorCode,
    EventKind,
    ExecutionObservation,
    RiskLevel,
)
from agentforge.schemas import (
    TestResults as PytestResultsSchema,
)
from agentforge.tools.execution_tools import (
    RUN_PYTEST_TOOL,
    RUN_PYTHON_SCRIPT_TOOL,
    RunPytestInput,
    RunPythonScriptInput,
    run_pytest_handler,
    run_python_script_handler,
)


def _ws(ctx: Any) -> Path:
    return ctx.workspace_manager.get(ctx.session_id)


# ---------------------------------------------------------------------------
# run_python_script
# ---------------------------------------------------------------------------


async def test_run_python_script_executes_and_captures_stdout(tool_ctx: Any) -> None:
    (_ws(tool_ctx) / "generated" / "hello.py").write_text(
        "print('hello from sandbox')\n"
    )
    out = await run_python_script_handler(
        RunPythonScriptInput(script_path="generated/hello.py"), tool_ctx
    )
    assert isinstance(out, ExecutionObservation)
    assert out.success is True
    assert out.exit_code == 0
    assert "hello from sandbox" in out.stdout_excerpt


async def test_run_python_script_passes_args(tool_ctx: Any) -> None:
    (_ws(tool_ctx) / "generated" / "argecho.py").write_text(
        "import sys; print(sys.argv[1:])\n"
    )
    out = await run_python_script_handler(
        RunPythonScriptInput(
            script_path="generated/argecho.py", args=["one", "two"]
        ),
        tool_ctx,
    )
    assert out.success is True
    assert "['one', 'two']" in out.stdout_excerpt


async def test_run_python_script_marks_failure_on_nonzero_exit(
    tool_ctx: Any,
) -> None:
    (_ws(tool_ctx) / "generated" / "boom.py").write_text(
        "raise SystemExit(2)\n"
    )
    out = await run_python_script_handler(
        RunPythonScriptInput(script_path="generated/boom.py"), tool_ctx
    )
    assert out.success is False
    assert out.exit_code == 2
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    kinds = [e.kind for e in events]
    assert EventKind.EXECUTION_FAILED in kinds
    failed = next(e for e in events if e.kind == EventKind.EXECUTION_FAILED)
    assert failed.payload["error_code"] == ErrorCode.GENERATED_CODE_FAILED.value


async def test_run_python_script_marks_timeout(tool_ctx: Any) -> None:
    (_ws(tool_ctx) / "generated" / "sleeper.py").write_text(
        "import time; time.sleep(3)\n"
    )
    out = await run_python_script_handler(
        RunPythonScriptInput(
            script_path="generated/sleeper.py", timeout_seconds=1
        ),
        tool_ctx,
    )
    assert out.success is False
    assert out.exit_code == -9  # SIGKILL sentinel from SandboxRunner
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    failed = next(e for e in events if e.kind == EventKind.EXECUTION_FAILED)
    assert failed.payload["timed_out"] is True
    assert failed.payload["error_code"] == ErrorCode.COMMAND_TIMEOUT.value


def test_run_python_script_definition_requires_approval() -> None:
    defn = RUN_PYTHON_SCRIPT_TOOL.definition
    assert defn.requires_approval is True
    assert defn.risk_level == RiskLevel.HIGH_WRITE
    assert defn.adr_override is None


# ---------------------------------------------------------------------------
# run_pytest
# ---------------------------------------------------------------------------


_TINY_TESTS = """
def test_passes():
    assert 1 + 1 == 2

def test_also_passes():
    assert 'a' in 'abc'

def test_fails():
    assert 1 == 2
"""


async def test_run_pytest_parses_pass_fail_mix(tool_ctx: Any) -> None:
    tests_dir = _ws(tool_ctx) / "generated" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_tiny.py").write_text(_TINY_TESTS)

    out = await run_pytest_handler(
        RunPytestInput(tests_path="generated/tests/", cwd="."),
        tool_ctx,
    )
    assert isinstance(out, PytestResultsSchema)
    assert out.passed_count == 2
    assert out.failed_count == 1
    assert out.total_count == 3
    names = sorted(t.name for t in out.per_test)
    assert any(n.endswith("::test_passes") for n in names)
    assert any(n.endswith("::test_fails") for n in names)
    # Humanised names drop the test_ prefix.
    assert any(
        t.humanised_name == "Passes" for t in out.per_test if t.name.endswith("::test_passes")
    )


async def test_run_pytest_emits_test_run_events(tool_ctx: Any) -> None:
    tests_dir = _ws(tool_ctx) / "generated" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_one.py").write_text("def test_a():\n    assert True\n")

    await run_pytest_handler(
        RunPytestInput(tests_path="generated/tests/", cwd="."),
        tool_ctx,
    )
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.TEST_RUN_STARTED in kinds
    assert EventKind.TEST_RUN_COMPLETED in kinds


async def test_run_pytest_handles_no_tests_collected(tool_ctx: Any) -> None:
    """An empty tests dir → pytest returns "no tests ran". Tool reports zero counts."""
    empty_dir = _ws(tool_ctx) / "generated" / "empty_tests"
    empty_dir.mkdir(parents=True, exist_ok=True)
    out = await run_pytest_handler(
        RunPytestInput(tests_path="generated/empty_tests/", cwd="."),
        tool_ctx,
    )
    assert out.passed_count == 0
    assert out.failed_count == 0
    assert out.total_count == 0


async def test_run_pytest_on_model_authored_generated_tests(tool_ctx: Any) -> None:
    """Run pytest against generated tests without seeding templates."""
    workspace = _ws(tool_ctx)
    tests_dir = workspace / "generated" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_agent.py").write_text(
        "def test_generated_check():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    out = await run_pytest_handler(
        RunPytestInput(tests_path="tests/", cwd="generated"),
        tool_ctx,
    )
    assert out.failed_count == 0, (
        f"unexpected failures; summary: {out.summary_line}\n"
        f"raw excerpt:\n{out.raw_output_excerpt[-500:]}"
    )
    assert out.passed_count == 1


def test_run_pytest_definition_requires_approval() -> None:
    defn = RUN_PYTEST_TOOL.definition
    assert defn.requires_approval is True
    assert defn.risk_level == RiskLevel.HIGH_WRITE
    assert defn.adr_override is None
