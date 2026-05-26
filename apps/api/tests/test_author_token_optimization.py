"""Author pipeline token/prompt optimization tests (not live token counts)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.config import Settings
from agentforge.orchestrator.author_llm_authoring import (
    _compact_pytest_repair_requirements,
    _compact_repair_failure_detail,
    _contract_for_prompt,
    _contract_prompt_rules,
    _contract_prompt_rules_core,
    _contract_stage_guidance,
    _is_small_author_workflow,
    _repair_failure_signature,
    _use_compact_contract_prompts,
)
from agentforge.schemas.author_output_contract import AuthorOutputContract


def _bank_schema_profile() -> dict[str, object]:
    return {
        "columns": [
            "transaction_id",
            "date",
            "account",
            "description",
            "counterparty",
            "amount",
            "currency",
            "reference",
            "direction",
        ],
        "row_count": 18,
        "upload_format": "csv",
        "agent_input_path": "uploads/bank_transaction_categorisation_demo.csv",
        "normalized_input_path": "uploads/bank_transaction_categorisation_demo.csv",
    }


def test_small_workflow_compact_prompt_defaults() -> None:
    settings = Settings()
    profile = _bank_schema_profile()
    assert settings.author_max_repair_attempts == 1
    assert settings.author_compact_contract_prompts is True
    assert _is_small_author_workflow(profile, settings)
    assert _use_compact_contract_prompts(profile, settings)


def test_large_workflow_skips_compact_prompts() -> None:
    settings = Settings(author_compact_contract_prompts=True)
    profile = {**_bank_schema_profile(), "row_count": 100}
    assert not _use_compact_contract_prompts(profile, settings)


def test_review_guidance_omits_schema_example_and_uses_core_rules() -> None:
    planning = _contract_stage_guidance(mode="planning")
    review = _contract_stage_guidance(mode="review")
    assert "exact_schema_example" in planning
    assert "exact_schema_example" not in review
    assert review["rules"] == _contract_prompt_rules_core()
    assert planning["rules"] == _contract_prompt_rules()
    assert len(json.dumps(review["rules"])) < len(json.dumps(planning["rules"]))


def test_schema_repair_guidance_is_trimmed_vs_planning() -> None:
    planning = _contract_stage_guidance(mode="planning")
    repair = _contract_stage_guidance(mode="schema_repair")
    assert "exact_schema_example" in planning
    assert "exact_schema_example" not in repair
    assert repair["rules"] == _contract_prompt_rules_core()
    assert planning["rules"] == _contract_prompt_rules()
    assert len(json.dumps(repair["rules"])) < len(json.dumps(planning["rules"]))


def test_compact_contract_for_prompt_is_smaller_than_full() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["transaction_id", "amount", "description"],
            "output_columns": ["transaction_id", "amount", "description", "category"],
            "required_output_columns": ["category"],
            "output_column_semantics": [
                {
                    "name": "category",
                    "description": "Assigned category label",
                    "producer_kind": "category_label",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Assign one category per row.",
                    "fallback_value_semantics": "Use Other when no rule matches.",
                }
            ],
            "validation_checks": [
                {
                    "check_id": "row_count",
                    "layer": "row_level",
                    "name": "Row count preserved",
                    "required": True,
                    "check_type": "row_count",
                }
            ],
            "allowed_enums": {"category": ["Revenue", "Other"]},
            "summary_metrics": ["transaction_count"],
            "requested_deliverables": [
                {
                    "name": "validation_report",
                    "description": "Validation report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                }
            ],
            "preserve_row_count": True,
        }
    )
    full_size = len(json.dumps(contract.model_dump(mode="json")))
    compact_size = len(json.dumps(_contract_for_prompt(contract, compact=True)))
    assert compact_size < full_size


def test_compact_repair_failure_detail_keeps_assertion_lines() -> None:
    detail = "\n".join(
        [
            "command=python generated/agent.py",
            "FAILED generated/tests/test_agent.py::test_confidence - AssertionError: bad",
            "assert 0.4 >= 0.8",
        ]
    )
    excerpt = _compact_repair_failure_detail(detail, max_chars=500)
    assert "FAILED generated/tests/test_agent.py::test_confidence" in excerpt
    assert "assert 0.4 >= 0.8" in excerpt
    assert "command=python" not in excerpt


def test_repair_failure_signature_is_stable_for_same_pytest_failures() -> None:
    detail = (
        "FAILED generated/tests/test_agent.py::test_a - AssertionError: one\n"
        "FAILED generated/tests/test_agent.py::test_b - AssertionError: two"
    )
    assert _repair_failure_signature(detail) == _repair_failure_signature(detail)


def test_compact_pytest_repair_requirements_are_shorter_than_full_block() -> None:
    compact = _compact_pytest_repair_requirements()
    assert len(compact) < 20
    assert any("clear_rule_confidence_08" in item for item in compact)


@pytest.mark.parametrize(
    ("row_count", "expected"),
    [(18, True), (25, True), (26, False), (None, False)],
)
def test_small_workflow_row_threshold(row_count: int | None, expected: bool) -> None:
    profile = dict(_bank_schema_profile())
    profile["row_count"] = row_count
    assert _is_small_author_workflow(profile, Settings()) is expected
