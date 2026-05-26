"""Generated pytest collection and pass gates."""

from __future__ import annotations

from uuid import uuid4

from agentforge.schemas.artifact import TestResults as PytestResultsSchema
from agentforge.tools.validation_tools import (
    GENERATED_PYTEST_MIN_COLLECTED,
    _parse_pytest_minimal,
    generated_pytest_gate_failed,
)


def test_parse_pytest_minimal_reads_collected_count_from_output() -> None:
    stdout = (
        "collecting ... collected 4 items\n\n"
        "generated/tests/test_agent.py::test_one PASSED [ 25%]\n"
        "generated/tests/test_agent.py::test_two PASSED [ 50%]\n"
        "generated/tests/test_agent.py::test_three PASSED [ 75%]\n"
        "generated/tests/test_agent.py::test_four PASSED [100%]\n\n"
        "============================== 4 passed in 0.12s =============================="
    )
    parsed = _parse_pytest_minimal(stdout)
    assert parsed.collected_count == 4
    assert parsed.passed_count == 4
    assert parsed.summary_line != "no tests collected"


def test_generated_pytest_gate_fails_on_zero_collected() -> None:
    parsed = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=0,
        failed_count=0,
        total_count=0,
        collected_count=0,
        summary_line="no tests collected",
        raw_output_excerpt="",
    )
    assert generated_pytest_gate_failed(parsed, 5) is True
    assert generated_pytest_gate_failed(parsed, 1) is True


def test_generated_pytest_gate_fails_on_missing_import_run() -> None:
    stdout = (
        "collecting ... collected 4 items\n\n"
        "generated/tests/test_agent.py::test_one FAILED [ 25%]\n"
        "generated/tests/test_agent.py::test_two FAILED [ 50%]\n"
        "generated/tests/test_agent.py::test_three FAILED [ 75%]\n"
        "generated/tests/test_agent.py::test_four FAILED [100%]\n\n"
        "============================== 4 failed in 0.12s =============================="
    )
    parsed = _parse_pytest_minimal(stdout)
    assert parsed.collected_count == 4
    assert generated_pytest_gate_failed(parsed, 1) is True


def test_generated_pytest_gate_passes_with_three_passing_tests() -> None:
    parsed = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=3,
        failed_count=0,
        total_count=3,
        collected_count=3,
        summary_line="3 passed in 0.1s",
        raw_output_excerpt="",
    )
    assert generated_pytest_gate_failed(parsed, 0) is False


def test_generated_pytest_gate_fails_on_workspace_pathing_errors() -> None:
    stdout = (
        "collecting ... collected 7 items\n\n"
        "generated/tests/test_agent.py::test_one ERROR [ 14%]\n"
        "generated/tests/test_agent.py::test_two ERROR [ 28%]\n"
        "generated/tests/test_agent.py::test_three ERROR [ 42%]\n\n"
        "E   AssertionError: Agent failed: python: can't open file "
        "'/private/tmp/pytest-of-user/pytest-1/test_one0/generated/agent.py': "
        "[Errno 2] No such file or directory\n\n"
        "======================== 7 warnings, 7 errors in 0.15s ========================="
    )
    parsed = _parse_pytest_minimal(stdout)
    assert parsed.collected_count == 7
    assert generated_pytest_gate_failed(parsed, 1) is True


def test_generated_pytest_gate_fails_when_only_two_tests_collected() -> None:
    parsed = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=2,
        failed_count=0,
        total_count=2,
        collected_count=2,
        summary_line="2 passed in 0.1s",
        raw_output_excerpt="",
    )
    assert generated_pytest_gate_failed(parsed, 0) is True
    assert GENERATED_PYTEST_MIN_COLLECTED == 3
