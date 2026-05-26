"""Unit tests for evidence-derived repair proposals (no fixture answer keys)."""

from __future__ import annotations

from pathlib import Path

from agentforge.orchestrator import repair_flow, repair_proposal

_REPO_ROOT = Path(__file__).resolve().parents[3]
_V2_AGENT = (
    _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v2" / "agent.py"
).read_text(encoding="utf-8")
_V1_AGENT = (
    _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v1" / "agent.py"
).read_text(encoding="utf-8")


def test_repair_modules_have_no_fixture_answer_table() -> None:
    """Repair logic must not key off fixture names or known patch tables."""
    forbidden = (
        "invoice_aging_v2",
        "invoice_aging_v1",
        "boundary_bug_31_to_30",
        "_KNOWN_PATCHES",
        "date_format_dmy_to_mdy",
    )
    for module in (repair_flow, repair_proposal):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token!r} found in {module.__name__}"


def test_boundary_inference_from_failing_evidence() -> None:
    evidence = repair_proposal.RepairEvidence(
        problem_statement=(
            "Invoices exactly 31 days overdue are in the 1-30 bucket "
            "instead of 31-60."
        ),
        files_inspected=[],
        agent_source=_V2_AGENT,
        agent_rel_path="working/invoice_aging_v2/agent.py",
        working_dir=Path("/tmp/unused"),
        failing_test_names=[
            "tests/test_agent.py::test_boundary_31_days_in_31_60_bucket",
        ],
        pytest_before_excerpt="FAILED test_boundary_31_days_in_31_60_bucket",
        failure_reproduced=True,
    )
    proposal = repair_proposal.infer_proposal_from_evidence(evidence)
    assert proposal is not None
    assert proposal.source == "evidence_inference"
    assert "if days_overdue <= 31:" in proposal.old_snippet
    assert "if days_overdue <= 30:" in proposal.new_snippet
    assert "31" in proposal.root_cause


def test_date_format_inference_from_sample_rows() -> None:
    evidence = repair_proposal.RepairEvidence(
        problem_statement="April invoices landed in the wrong aging bucket.",
        files_inspected=[],
        agent_source=_V1_AGENT,
        agent_rel_path="working/agent.py",
        working_dir=Path("/tmp/unused"),
        failing_test_names=["tests/test_aging.py::test_april_invoices_in_first_bucket"],
        failure_reproduced=True,
        sample_date_rows=[
            ("INV-2026-04-03-001", "04-03-2026"),
            ("INV-2026-04-15-004", "04-15-2026"),
            ("INV-2026-04-22-006", "04-22-2026"),
        ],
    )
    proposal = repair_proposal.infer_proposal_from_evidence(evidence)
    assert proposal is not None
    assert '"%d-%m-%Y"' in proposal.old_snippet
    assert '"%m-%d-%Y"' in proposal.new_snippet


def test_validate_rejects_ambiguous_snippet(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text("if x <= 1:\n    pass\nif x <= 1:\n    pass\n", encoding="utf-8")
    evidence = repair_proposal.RepairEvidence(
        problem_statement="duplicate branches",
        files_inspected=[],
        agent_source=agent.read_text(encoding="utf-8"),
        agent_rel_path="agent.py",
        working_dir=tmp_path,
        failing_test_names=["tests/test_x.py::test_x"],
        failure_reproduced=True,
    )
    proposal = repair_proposal.RepairProposal(
        root_cause="duplicate if x <= 1 lines in test_x failure context",
        target_file="agent.py",
        old_snippet="if x <= 1:",
        new_snippet="if x <= 0:",
        risk="low",
        why_this_fix="test_x shows duplicate boundary failure evidence",
        source="evidence_inference",
    )
    result = repair_proposal.validate_repair_proposal(
        proposal, workspace=tmp_path, evidence=evidence
    )
    assert not result.ok
    assert "more than once" in result.message


def test_refresh_repaired_agent_replaces_stale_boundary_comment() -> None:
    source = _V2_AGENT.replace("if days_overdue <= 31:", "if days_overdue <= 30:", 1)
    updated = repair_proposal._refresh_repaired_agent_docstring(source)
    assert "BOUNDARY BUG" not in updated
    assert "Correct boundary:" in updated
    assert "Invoices exactly 31 days overdue belong in the 31-60 bucket." in updated
    assert "if days_overdue <= 30:" in updated
