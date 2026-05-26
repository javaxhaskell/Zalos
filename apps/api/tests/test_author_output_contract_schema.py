"""AuthorOutputContract schema accepts structured finance workflow specs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentforge.models import FakeModelClient, ModelResponse, TextBlock
from agentforge.orchestrator.author_llm_authoring import (
    _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES,
    _BUNDLED_BANK_REFERENCE_WARNING,
    _bundled_bank_reference_contract_scaffold,
    ContractSemanticValidationError,
    _allowed_enums_guidance,
    _artifact_path_namespace_guidance,
    _calculated_field_guidance,
    _contract_finalization_guidance,
    _contract_planning_failed_result,
    _contract_prompt_rules,
    _contract_return_shape_guidance,
    _contract_schema_dialect_guidance,
    _contract_review_failed_result,
    _contract_top_level_skeleton,
    _contract_validation_issue_details,
    _exception_rule_guidance,
    _extract_json_payload_with_hygiene,
    _finalize_contract_payload,
    _normalize_contract_output_artifact_path,
    _output_column_semantics_guidance,
    _passthrough_output_column_semantics_spec,
    _requested_deliverables_guidance,
    _required_output_semantics_guidance,
    _sanitize_allowed_enums_payload,
    _sanitize_contract_alternate_dialect_payload,
    _sanitize_formula_disciplined_calculated_fields_payload,
    _sanitize_prompt_explicit_category_enum_payload,
    _sanitize_contract_shape_payload,
    _sanitize_requested_deliverables_explicit_source_payload,
    _sanitize_required_output_column_semantics_payload,
    _sanitize_validation_checks_payload,
    _unwrap_allowed_enum_entry,
    _validate_contract_or_repair,
    _validate_contract_payload_strict,
    _validation_check_guidance,
    run_model_authoring_pipeline,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ErrorCode, EventKind, Workflow
from agentforge.schemas.author_output_contract import (
    AuthorOutputContract,
    DeliverableSpec,
    ExceptionRuleSpec,
    OutputFileSpec,
    OutputColumnSemanticsSpec,
    SummaryMetricSpec,
    ValidationCheckSpec,
    deliverable_label,
    output_file_path,
)
from tests.author_model_fixtures import model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"
_FIXTURE_1CB836C2 = (
    Path(__file__).resolve().parent
    / "fixtures/author_contract_candidates/ar_planning_missing_passthrough_semantics_1cb836c2.json"
)
_FIXTURE_6D5F2D37 = (
    Path(__file__).resolve().parent
    / "fixtures/author_contract_candidates/ar_planning_requested_deliverables_explicit_summary_exceptions_6d5f2d37.json"
)
_FIXTURE_86DD6874 = (
    Path(__file__).resolve().parent
    / "fixtures/author_contract_candidates/ar_review_alternate_schema_dialect_86dd6874.json"
)
_FIXTURE_D1031516 = (
    Path(__file__).resolve().parent
    / "fixtures/author_contract_candidates/ar_review_generic_schema_and_numeric_enums_d1031516.json"
)
_FIXTURE_VENDOR_PAYMENT_FORMULA = (
    Path(__file__).resolve().parent
    / "fixtures/author_contract_candidates/vendor_payment_non_arithmetic_formula_recommended_payment_date.json"
)
_FIXTURE_9184AA74 = (
    Path(__file__).resolve().parent
    / "fixtures/author_contract_candidates/ar_review_input_column_drift_9184aa74.json"
)


def _bank_reference_schema_profile() -> dict[str, object]:
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
        "sample_rows": [],
    }


def _apply_live_contract_sanitizers(
    payload: dict[str, object],
    *,
    schema_profile: dict[str, object],
    user_description: str = "",
    contract_reference: dict[str, object] | None = None,
    include_passthrough_semantics: bool = True,
    include_explicit_deliverable_source: bool = True,
    include_alternate_dialect: bool = True,
) -> dict[str, object]:
    sanitized = dict(payload)
    if include_alternate_dialect:
        sanitized = _sanitize_contract_alternate_dialect_payload(
            sanitized,
            schema_profile=schema_profile,
            contract_reference=contract_reference,
        )
    sanitized, _ = _sanitize_contract_shape_payload(
        payload=sanitized,
        schema_profile=schema_profile,
    )
    sanitized = _sanitize_validation_checks_payload(sanitized)
    sanitized = _sanitize_allowed_enums_payload(sanitized)
    sanitized = _sanitize_prompt_explicit_category_enum_payload(
        sanitized,
        user_description=user_description,
    )
    if include_passthrough_semantics:
        sanitized = _sanitize_required_output_column_semantics_payload(
            sanitized,
            schema_profile=schema_profile,
        )
    sanitized = _sanitize_formula_disciplined_calculated_fields_payload(
        sanitized,
        schema_profile=schema_profile,
    )
    if include_explicit_deliverable_source and user_description:
        sanitized = _sanitize_requested_deliverables_explicit_source_payload(
            sanitized,
            user_description=user_description,
        )
    if include_alternate_dialect and include_passthrough_semantics and include_explicit_deliverable_source:
        sanitized = _finalize_contract_payload(
            sanitized,
            schema_profile=schema_profile,
            user_description=user_description,
            contract_reference=contract_reference,
        )
    return sanitized


def _response(id_: str, payload: object) -> ModelResponse:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return ModelResponse(
        id=id_,
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
    )


def _payload(response: ModelResponse) -> dict:
    return json.loads(response.text)


def _purposes(event_log: EventLog, sid) -> list[str]:
    return [
        str(event.payload.get("purpose"))
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    ]


def _base_script() -> list[ModelResponse]:
    return model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )


def _run_pipeline(workspaces_root: Path, script: list[ModelResponse]):
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_schema",
            model_client=FakeModelClient(script=script),
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    return result, event_log, sid, workspace


def _minimal_contract_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflow_type": "finance_exception_review",
        "workflow_confidence": 0.8,
        "build_mode": "llm_custom",
        "input_file": "uploads/input.csv",
        "input_format": "csv",
        "row_level_output_file": "outputs/output.csv",
        "summary_output_files": [],
        "exception_output_files": [],
        "input_columns": ["invoice_id", "amount"],
        "output_columns": ["invoice_id", "amount", "issue_flag"],
        "required_output_columns": ["issue_flag"],
        "output_column_semantics": [
            {
                "name": "issue_flag",
                "description": "Non-empty review flag for every output row",
                "producer_kind": "exception_flag",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit a non-empty review flag for every output row.",
                "fallback_value_semantics": "Use an explicit default non-exception flag when no exception rule matches.",
            }
        ],
        "exception_rules": [],
        "validation_checks": [],
        "clarification_questions": [],
        "unsupported_reasons": [],
        "preserve_row_count": True,
    }
    payload.update(overrides)
    return payload


def test_structured_summary_output_file_spec_is_accepted() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            summary_output_files=[
                OutputFileSpec(
                    path="outputs/remittance_summary.csv",
                    description="Totals by remittance batch",
                    required_columns=["remittance_id", "paid_total"],
                )
            ]
        )
    )
    assert output_file_path(contract.summary_output_files[0]) == "outputs/remittance_summary.csv"
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "outputs/remittance_summary.csv",
    ]


def test_summary_metrics_accepts_plain_strings() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            summary_metrics=["row_count", "amount_total"],
        )
    )
    assert contract.summary_metrics == ["row_count", "amount_total"]


def test_summary_metrics_accepts_structured_summary_metric_spec() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            summary_group_keys=["category"],
            summary_metrics=[
                SummaryMetricSpec(
                    name="amount_total",
                    metric_type="sum",
                    source_column="amount",
                    description="Total amount by category",
                )
            ],
        )
    )
    metric = contract.summary_metrics[0]
    assert isinstance(metric, SummaryMetricSpec)
    assert metric.name == "amount_total"
    assert metric.metric_type == "sum"


def test_invalid_summary_metric_object_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthorOutputContract.model_validate(
            _minimal_contract_payload(
                summary_metrics=[
                    {
                        "metric_type": "sum",
                        "source_column": "amount",
                    }
                ]
            )
        )


def test_summary_metrics_accepts_formula_input_columns_and_output_column() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            summary_metrics=[
                {
                    "name": "category_counts",
                    "formula": "count_by_category(category)",
                    "input_columns": ["category"],
                    "output_column": "category_count",
                }
            ]
        )
    )
    metric = contract.summary_metrics[0]
    assert isinstance(metric, SummaryMetricSpec)
    assert metric.formula == "count_by_category(category)"
    assert metric.input_columns == ["category"]
    assert metric.output_column == "category_count"


def test_summary_metrics_condition_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthorOutputContract.model_validate(
            _minimal_contract_payload(
                summary_metrics=[
                    {
                        "name": "uncertain_rows_count",
                        "metric_type": "count",
                        "condition": "confidence < 0.8",
                    }
                ]
            )
        )


def test_plain_string_deliverables_are_not_treated_as_required() -> None:
    from agentforge.schemas.author_output_contract import deliverable_required

    assert deliverable_required("outputs/output.csv") is False
    assert deliverable_required(
        DeliverableSpec(
            name="validation_report",
            output_path="reports/validation_report.md",
            required=True,
        )
    )


def test_requested_deliverables_accepts_plain_strings() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            requested_deliverables=["outputs/output.csv", "reports/validation_report.md"],
        )
    )
    assert contract.requested_deliverables == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]


def test_requested_deliverables_accepts_deliverable_spec_objects() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            requested_deliverables=[
                "outputs/output.csv",
                DeliverableSpec(
                    name="validation_report",
                    description="Validation report explaining rules and counts",
                    output_path="reports/validation_report.md",
                    required=True,
                ),
            ],
        )
    )
    second = contract.requested_deliverables[1]
    assert isinstance(second, DeliverableSpec)
    assert deliverable_label(second) == "reports/validation_report.md"


def test_bundled_bank_reference_contract_scaffold_validates_strictly() -> None:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    assert isinstance(contract, AuthorOutputContract)
    assert contract.input_file == "uploads/bank_transaction_categorisation_demo.csv"
    assert contract.row_level_output_file == "outputs/output.csv"
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    assert _BUNDLED_BANK_REFERENCE_WARNING in contract.warnings
    assert contract.golden_comparison_requirement == "required"
    assert contract.golden_output_path == "evals/golden_output.csv"


def test_bundled_bank_reference_contract_scaffold_preserves_exact_categories() -> None:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    assert contract.allowed_enums["category"] == list(
        _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES
    )
    assert contract.required_output_columns == [
        "category",
        "rule_matched",
        "rule_used",
        "confidence_score",
        "review_required",
    ]


def test_sanitize_contract_shape_payload_repairs_deliverable_shape_and_missing_input_file() -> None:
    payload = _minimal_contract_payload(
        input_file=None,
        requested_deliverables=[
            {"path": "outputs/output.csv", "required": True},
            {
                "path": "reports/validation_report.md",
                "required": True,
                "description": "Validation report explaining rules and counts",
            },
        ],
    )
    payload["output_format"] = "csv"

    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={
            "agent_input_path": "uploads/normalised_input.csv",
            "normalized_input_path": "uploads/normalised_input.csv",
        },
    )

    assert changed is True
    assert sanitized["input_file"] == "uploads/normalised_input.csv"
    assert "output_format" not in sanitized
    assert sanitized["requested_deliverables"] == [
        {
            "name": "output",
            "output_path": "outputs/output.csv",
            "required": True,
        },
        {
            "name": "validation_report",
            "description": "Validation report explaining rules and counts",
            "output_path": "reports/validation_report.md",
            "required": True,
        },
    ]


def test_sanitize_contract_shape_payload_does_not_add_input_file_without_known_profile() -> None:
    payload = _minimal_contract_payload()
    payload.pop("input_file", None)

    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"columns": ["invoice_id", "amount"]},
    )

    assert changed is False
    assert "input_file" not in sanitized
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_sanitize_contract_shape_payload_adds_minimal_calculated_field_description() -> None:
    payload = _minimal_contract_payload(
        calculated_fields=[
            {"name": "residual_balance", "formula": "amount"},
        ]
    )
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"agent_input_path": "uploads/input.csv"},
    )
    assert changed is True
    assert sanitized["calculated_fields"][0]["description"] == "Derived field residual balance"


def test_sanitize_contract_shape_payload_repairs_exception_rule_issue_flag_field() -> None:
    payload = _minimal_contract_payload(
        exception_rules=[
            {
                "name": "requires_review",
                "condition": "amount > 0",
                "issue_flag": "Residual balance is positive.",
            }
        ]
    )
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"agent_input_path": "uploads/input.csv"},
    )
    assert changed is True
    rule = sanitized["exception_rules"][0]
    assert "issue_flag" not in rule
    assert rule["output_column"] == "issue_flag"
    assert rule["reason"] == "Residual balance is positive."


def test_sanitize_contract_shape_payload_removes_generic_forbidden_top_level_flags() -> None:
    payload = _minimal_contract_payload(
        preserve_original_data=True,
        enable_logging=True,
        enable_debugging=True,
    )
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"agent_input_path": "uploads/input.csv"},
    )
    assert changed is True
    assert "preserve_original_data" not in sanitized
    assert "enable_logging" not in sanitized
    assert "enable_debugging" not in sanitized


def test_contract_prompt_rules_include_artifact_path_namespace_guidance() -> None:
    rules = "\n".join(_contract_prompt_rules())
    assert "outputs/" in rules
    assert "reports/" in rules
    assert "summaries/" in rules
    assert "summary/" in rules
    guidance = _artifact_path_namespace_guidance()
    assert guidance["allowed_output_prefixes"] == ["outputs/", "reports/"]
    assert "summaries/<filename> -> outputs/<filename>" in guidance["mapping_rules"]


def test_normalize_contract_output_artifact_path_rewrites_summaries_and_summary() -> None:
    path, changed = _normalize_contract_output_artifact_path(
        "summaries/summary_by_category.json"
    )
    assert changed is True
    assert path == "outputs/summary_by_category.json"
    path, changed = _normalize_contract_output_artifact_path("summary/customer_aging.csv")
    assert changed is True
    assert path == "outputs/customer_aging.csv"


def test_normalize_contract_output_artifact_path_preserves_allowed_prefixes() -> None:
    for allowed in (
        "outputs/exceptions.csv",
        "reports/validation_report.md",
        "uploads/normalised_input.csv",
        "generated/agent.py",
        "generated/tests/test_agent.py",
    ):
        path, changed = _normalize_contract_output_artifact_path(allowed)
        assert changed is False
        assert path == allowed


def test_sanitize_contract_shape_payload_normalizes_summaries_summary_output_paths() -> None:
    payload = _minimal_contract_payload(
        summary_output_files=["summaries/summary_by_category.json"],
        exception_output_files=["outputs/exceptions.csv"],
        requested_deliverables=[
            {"output_path": "summary/customer_aging.csv", "name": "customer_aging"},
            "reports/validation_report.md",
        ],
    )
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"agent_input_path": "uploads/normalised_input.csv"},
    )
    assert changed is True
    assert sanitized["summary_output_files"] == ["outputs/summary_by_category.json"]
    assert sanitized["exception_output_files"] == ["outputs/exceptions.csv"]
    assert sanitized["requested_deliverables"][0]["output_path"] == "outputs/customer_aging.csv"
    assert sanitized["requested_deliverables"][1] == "reports/validation_report.md"


def test_normalize_contract_output_artifact_path_rewrites_exceptions() -> None:
    path, changed = _normalize_contract_output_artifact_path(
        "exceptions/investigation_required.csv"
    )
    assert changed is True
    assert path == "outputs/investigation_required.csv"


def test_contract_prompt_rules_include_allowed_enums_plain_list_guidance() -> None:
    rules = "\n".join(_contract_prompt_rules())
    assert "allowed_enums must map output column names to plain JSON arrays" in rules
    guidance = _allowed_enums_guidance()
    assert guidance["valid_shape"]["allowed_enums"]["status"] == ["open", "closed"]
    assert {"status": {"values": ["open", "closed"]}} in guidance["invalid_shapes"]
    assert any("simple numeric or boolean scalar labels" in rule for rule in guidance["planning_rules"])


def test_sanitize_allowed_enums_preserves_plain_lists() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={"status": ["open", "closed"]},
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["status"] == ["open", "closed"]


@pytest.mark.parametrize(
    ("column", "wrapped", "expected"),
    [
        ("issue_flag", {"values": ["Y", "N"]}, ["Y", "N"]),
        ("status", {"allowed_values": ["open", "closed"]}, ["open", "closed"]),
        ("category", {"enum_values": ["A", "B"]}, ["A", "B"]),
        ("bucket", {"categories": ["0-30", "31-60"]}, ["0-30", "31-60"]),
    ],
)
def test_sanitize_allowed_enums_unwraps_wrapper_dicts(
    column: str, wrapped: dict[str, list[str]], expected: list[str]
) -> None:
    payload = _minimal_contract_payload(allowed_enums={column: wrapped})
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"][column] == expected


def test_sanitize_allowed_enums_unwraps_values_with_metadata() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={
            "issue_flag": {
                "values": ["Y", "N"],
                "description": "flag values",
            }
        }
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["issue_flag"] == ["Y", "N"]


def test_sanitize_allowed_enums_stringifies_scalar_lists_without_inventing_labels() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={"aging_bucket": [30, 60, 90, 120]},
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["aging_bucket"] == ["30", "60", "90", "120"]


def test_sanitize_allowed_enums_stringifies_wrapped_scalar_lists() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={"aging_bucket": {"values": [30, 60, 90, 120]}},
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["aging_bucket"] == ["30", "60", "90", "120"]


def test_sanitize_allowed_enums_leaves_unrelated_dict_shape_for_strict_validation() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={"status": {"labels": ["open", "closed"]}},
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["status"] == {"labels": ["open", "closed"]}
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_sanitize_allowed_enums_leaves_conflicting_wrapper_keys_for_strict_validation() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={
            "status": {
                "values": ["open", "closed"],
                "allowed_values": ["draft", "posted"],
            }
        }
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["status"] == {
        "values": ["open", "closed"],
        "allowed_values": ["draft", "posted"],
    }
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_sanitize_allowed_enums_leaves_ambiguous_scalar_object_mixes_for_strict_validation() -> None:
    payload = _minimal_contract_payload(
        allowed_enums={"aging_bucket": [30, {"upper_bound": 60}]},
    )
    sanitized = _sanitize_allowed_enums_payload(payload)
    assert sanitized["allowed_enums"]["aging_bucket"] == [30, {"upper_bound": 60}]
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_unwrap_allowed_enum_entry_unwraps_ar_style_wrappers() -> None:
    unwrapped, changed = _unwrap_allowed_enum_entry({"values": ["Y", "N"]})
    assert changed is True
    assert unwrapped == ["Y", "N"]
    unwrapped, changed = _unwrap_allowed_enum_entry({"values": [0, 30, 60, 90, ">90"]})
    assert changed is True
    assert unwrapped == [0, 30, 60, 90, ">90"]


def test_output_column_semantics_guidance_requires_semantics_for_every_required_column() -> None:
    guidance = _output_column_semantics_guidance()
    rules = "\n".join(_contract_prompt_rules())
    assert "Every required_output_columns entry must have a matching OutputColumnSemanticsSpec" in "\n".join(
        guidance["planning_rules"]
    )
    assert "Do not omit output_column_semantics for passthrough columns" in "\n".join(
        guidance["planning_rules"]
    )
    assert "copied_input" in guidance["producer_kind_examples"]
    assert "required_derived_output_column" in guidance["valid_examples"]
    assert "Do not list a column as required unless its output_column_semantics are defined" in rules


def test_passthrough_output_column_semantics_spec_uses_copied_input_shape() -> None:
    spec = _passthrough_output_column_semantics_spec("customer_id")
    assert spec["name"] == "customer_id"
    assert spec["producer_kind"] == "copied_input"
    assert "customer_id" in spec["description"]


def test_sanitize_required_output_column_semantics_adds_exact_input_matches_only() -> None:
    payload = _minimal_contract_payload(
        input_columns=["invoice_id", "amount"],
        required_output_columns=["invoice_id", "amount", "derived_flag"],
        output_column_semantics=[],
    )
    sanitized = _sanitize_required_output_column_semantics_payload(
        payload,
        schema_profile={"columns": ["invoice_id", "amount"]},
    )
    by_name = {item["name"]: item for item in sanitized["output_column_semantics"]}
    assert by_name["invoice_id"]["producer_kind"] == "copied_input"
    assert by_name["amount"]["producer_kind"] == "copied_input"
    assert "derived_flag" not in by_name


def test_sanitize_required_output_column_semantics_preserves_existing_entries() -> None:
    existing = {
        "name": "invoice_id",
        "description": "Existing semantics",
        "producer_kind": "copied_input",
        "required": True,
        "nullable": False,
        "allow_empty_string": False,
        "row_semantics": "Keep this exact row semantics.",
        "fallback_value_semantics": "Keep this exact fallback semantics.",
    }
    payload = _minimal_contract_payload(
        input_columns=["invoice_id"],
        required_output_columns=["invoice_id"],
        output_column_semantics=[existing],
    )
    sanitized = _sanitize_required_output_column_semantics_payload(
        payload,
        schema_profile={"columns": ["invoice_id"]},
    )
    assert sanitized["output_column_semantics"] == [existing]


def test_sanitize_required_output_column_semantics_does_not_fuzzy_match_near_names() -> None:
    payload = _minimal_contract_payload(
        input_columns=["customer_id"],
        required_output_columns=["customer_ids"],
        output_column_semantics=[],
    )
    sanitized = _sanitize_required_output_column_semantics_payload(
        payload,
        schema_profile={"columns": ["customer_id"]},
    )
    assert sanitized["output_column_semantics"] == []
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_sanitize_required_output_column_semantics_does_not_alter_other_contract_fields() -> None:
    payload = _minimal_contract_payload(
        input_columns=["invoice_id"],
        required_output_columns=["invoice_id", "issue_flag"],
        allowed_enums={"issue_flag": ["no", "yes"]},
        validation_checks=[
            {
                "check_id": "check_issue_flag",
                "layer": "row",
                "name": "Issue flag present",
                "required": True,
                "check_type": "non_null",
                "output_column": "issue_flag",
            }
        ],
        requested_deliverables=["outputs/output.csv"],
        output_column_semantics=[],
    )
    sanitized = _sanitize_required_output_column_semantics_payload(
        payload,
        schema_profile={
            "columns": ["invoice_id"],
            "agent_input_path": "uploads/input.csv",
        },
    )
    assert sanitized["input_file"] == "uploads/input.csv"
    assert sanitized["allowed_enums"] == {"issue_flag": ["no", "yes"]}
    assert sanitized["validation_checks"][0]["check_id"] == "check_issue_flag"
    assert sanitized["requested_deliverables"] == ["outputs/output.csv"]
    assert sanitized["required_output_columns"] == ["invoice_id", "issue_flag"]


def test_ar_planning_fixture_1cb836c2_reproduces_missing_passthrough_semantics_without_fix() -> None:
    if not _FIXTURE_1CB836C2.is_file():
        pytest.skip("1cb836c2 fixture unavailable")
    fixture = json.loads(_FIXTURE_1CB836C2.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
        include_passthrough_semantics=False,
    )
    with pytest.raises(ValueError, match="missing output_column_semantics for required columns"):
        _validate_contract_payload_strict(sanitized)


def test_ar_planning_fixture_1cb836c2_passes_after_passthrough_semantics_repair() -> None:
    if not _FIXTURE_1CB836C2.is_file():
        pytest.skip("1cb836c2 fixture unavailable")
    fixture = json.loads(_FIXTURE_1CB836C2.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
        include_passthrough_semantics=True,
    )
    by_name = {
        item["name"]: item
        for item in sanitized["output_column_semantics"]
        if isinstance(item, dict) and item.get("name")
    }
    repaired = [
        column
        for column in fixture["contract"]["required_output_columns"]
        if column in fixture["contract"]["input_columns"]
        and column not in {s["name"] for s in fixture["contract"]["output_column_semantics"]}
    ]
    assert repaired
    for column in repaired:
        assert by_name[column]["producer_kind"] == "copied_input"
    contract = _validate_contract_payload_strict(sanitized)
    assert contract.input_format == "xlsx"
    assert contract.input_file == fixture["schema_profile"]["agent_input_path"]


def test_requested_deliverables_guidance_allows_explicit_summary_and_exception_files() -> None:
    rules = "\n".join(_contract_prompt_rules())
    guidance = _requested_deliverables_guidance()
    assert "separate exceptions file" in rules.lower()
    assert "summary csv" in rules.lower() or "summary csv or json" in rules.lower()
    assert any("separate exception" in rule.lower() for rule in guidance["planning_rules"])
    assert any("summary csv" in rule.lower() for rule in guidance["planning_rules"])


def test_ar_planning_fixture_6d5f2d37_reproduces_requested_deliverables_rejection_without_fix() -> None:
    if not _FIXTURE_6D5F2D37.is_file():
        pytest.skip("6d5f2d37 fixture unavailable")
    fixture = json.loads(_FIXTURE_6D5F2D37.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
        user_description=fixture["user_description"],
        include_explicit_deliverable_source=False,
    )
    with pytest.raises(ContractSemanticValidationError, match="requested_deliverables"):
        _validate_contract_payload_strict(sanitized)


def test_ar_planning_fixture_6d5f2d37_passes_after_explicit_deliverable_recognition() -> None:
    if not _FIXTURE_6D5F2D37.is_file():
        pytest.skip("6d5f2d37 fixture unavailable")
    fixture = json.loads(_FIXTURE_6D5F2D37.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
        user_description=fixture["user_description"],
        include_explicit_deliverable_source=True,
    )
    deliverables = {
        item["output_path"]: item
        for item in sanitized["requested_deliverables"]
        if isinstance(item, dict) and item.get("output_path")
    }
    assert deliverables["outputs/summary_by_customer_and_aging_bucket.csv"]["source"] == "user_explicit"
    assert deliverables["outputs/investigation_required.csv"]["source"] == "user_explicit"
    contract = _validate_contract_payload_strict(sanitized)
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "outputs/summary_by_customer_and_aging_bucket.csv",
        "outputs/investigation_required.csv",
    ]


def test_ungrounded_summary_required_deliverable_still_rejected() -> None:
    payload = _minimal_contract_payload(
        summary_output_files=[
            {
                "path": "outputs/summary_by_category.csv",
                "description": "Category counts",
                "required_columns": ["category", "count"],
            }
        ],
        requested_deliverables=[
            "outputs/output.csv",
            {
                "name": "summary_by_category",
                "output_path": "outputs/summary_by_category.csv",
                "required": True,
            },
        ],
    )
    sanitized = _apply_live_contract_sanitizers(
        payload,
        schema_profile={"columns": ["a"], "upload_format": "csv", "agent_input_path": "uploads/input.csv"},
        user_description="Categorise transactions and produce a clean row-level output.",
        include_explicit_deliverable_source=True,
    )
    with pytest.raises(ContractSemanticValidationError, match="requested_deliverables"):
        _validate_contract_payload_strict(sanitized)


def test_ungrounded_exception_required_deliverable_still_rejected() -> None:
    payload = _minimal_contract_payload(
        exception_output_files=[
            {
                "path": "outputs/exceptions.csv",
                "description": "Rows needing review",
                "required_columns": ["invoice_id", "issue_flag"],
            }
        ],
        requested_deliverables=[
            "outputs/output.csv",
            {
                "name": "exceptions",
                "output_path": "outputs/exceptions.csv",
                "required": True,
            },
        ],
    )
    sanitized = _apply_live_contract_sanitizers(
        payload,
        schema_profile={"columns": ["invoice_id"], "upload_format": "csv", "agent_input_path": "uploads/input.csv"},
        user_description="Prepare a clean row-level output for finance review.",
        include_explicit_deliverable_source=True,
    )
    with pytest.raises(ContractSemanticValidationError, match="requested_deliverables"):
        _validate_contract_payload_strict(sanitized)


def test_explicit_deliverable_recognition_does_not_ground_unrequested_category() -> None:
    payload = _minimal_contract_payload(
        summary_output_files=[
            {
                "path": "outputs/summary_by_category.csv",
                "description": "Category counts",
                "required_columns": ["category", "count"],
            }
        ],
        exception_output_files=[
            {
                "path": "outputs/exceptions.csv",
                "description": "Rows needing review",
                "required_columns": ["invoice_id", "issue_flag"],
            }
        ],
        requested_deliverables=[
            "outputs/output.csv",
            {
                "name": "summary_by_category",
                "output_path": "outputs/summary_by_category.csv",
                "required": True,
            },
            {
                "name": "exceptions",
                "output_path": "outputs/exceptions.csv",
                "required": True,
            },
        ],
    )
    sanitized = _apply_live_contract_sanitizers(
        payload,
        schema_profile={"columns": ["a"], "upload_format": "csv", "agent_input_path": "uploads/input.csv"},
        user_description="Produce a separate exceptions file and a clean row-level output.",
        include_explicit_deliverable_source=True,
    )
    deliverables = {
        item["output_path"]: item
        for item in sanitized["requested_deliverables"]
        if isinstance(item, dict) and item.get("output_path")
    }
    assert deliverables["outputs/exceptions.csv"]["source"] == "user_explicit"
    assert deliverables["outputs/summary_by_category.csv"].get("source") != "user_explicit"
    with pytest.raises(ContractSemanticValidationError, match="requested_deliverables"):
        _validate_contract_payload_strict(sanitized)


def test_contract_schema_dialect_guidance_forbids_alternate_review_fields() -> None:
    rules = "\n".join(_contract_prompt_rules())
    guidance = _contract_schema_dialect_guidance()
    assert "row_level_output_path" in rules
    assert "calculated_field_definitions" in rules
    assert "input_path" in rules
    assert "row_level_output_path" in guidance["forbidden_alternate_fields"]
    assert "input_path" in guidance["forbidden_generic_review_fields"]
    assert guidance["canonical_field_mappings"]["calculated_field_definitions"] == "calculated_fields"


def test_contract_prompt_rules_require_formula_discipline_and_input_faithfulness() -> None:
    rules = "\n".join(_contract_prompt_rules())
    assert "input_columns must come from the uploaded input schema" in rules
    assert "Non-arithmetic derived outputs such as *_date" in rules
    assert "review must preserve the planned input column universe" in rules


def test_contract_finalization_guidance_reports_non_arithmetic_formula_candidates() -> None:
    if not _FIXTURE_VENDOR_PAYMENT_FORMULA.is_file():
        pytest.skip("vendor-payment formula fixture unavailable")
    fixture = json.loads(_FIXTURE_VENDOR_PAYMENT_FORMULA.read_text(encoding="utf-8"))
    guidance = _contract_finalization_guidance(
        payload=fixture["contract"],
        schema_profile=fixture["schema_profile"],
    )
    assert guidance["uploaded_input_columns"] == fixture["schema_profile"]["columns"]
    assert guidance["invalid_non_arithmetic_formulas"] == [
        {
            "name": "recommended_payment_date",
            "classification": "date_derived",
            "formula": "recommend_payment_date(due_date, vendor_terms_days)",
        }
    ]


def test_ar_review_fixture_86dd6874_reproduces_alternate_schema_dialect_failure_without_fix() -> None:
    if not _FIXTURE_86DD6874.is_file():
        pytest.skip("86dd6874 fixture unavailable")
    fixture = json.loads(_FIXTURE_86DD6874.read_text(encoding="utf-8"))
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(fixture["contract"])


def test_ar_review_fixture_86dd6874_passes_after_alternate_dialect_normalization() -> None:
    if not _FIXTURE_86DD6874.is_file():
        pytest.skip("86dd6874 fixture unavailable")
    fixture = json.loads(_FIXTURE_86DD6874.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    assert isinstance(sanitized["output_column_semantics"], list)
    assert sanitized["row_level_output_file"] == "outputs/row_level_output.csv"
    assert sanitized["calculated_fields"]
    assert "calculated_field_definitions" not in sanitized
    assert "row_level_output_path" not in sanitized
    contract = _validate_contract_payload_strict(sanitized)
    assert contract.workflow_type == fixture["contract_reference"]["workflow_type"]


def test_ar_review_fixture_d1031516_generic_review_remains_invalid_after_reference_merge() -> None:
    if not _FIXTURE_D1031516.is_file():
        pytest.skip("d1031516 fixture unavailable")
    fixture = json.loads(_FIXTURE_D1031516.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["review_contract"],
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    assert sanitized["row_level_output_file"] == fixture["contract_reference"]["row_level_output_file"]
    assert sanitized["requested_deliverables"] == fixture["contract_reference"]["requested_deliverables"]
    assert "input_path" in sanitized
    assert "workflow_name" in sanitized
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_ar_review_fixture_d1031516_repair_candidate_fails_without_scalar_enum_normalization() -> None:
    if not _FIXTURE_D1031516.is_file():
        pytest.skip("d1031516 fixture unavailable")
    fixture = json.loads(_FIXTURE_D1031516.read_text(encoding="utf-8"))
    sanitized = _sanitize_contract_alternate_dialect_payload(
        fixture["repair_contract"],
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    sanitized, _ = _sanitize_contract_shape_payload(
        payload=sanitized,
        schema_profile=fixture["schema_profile"],
    )
    sanitized = _sanitize_validation_checks_payload(sanitized)
    sanitized = _sanitize_required_output_column_semantics_payload(
        sanitized,
        schema_profile=fixture["schema_profile"],
    )
    with pytest.raises((ValidationError, ValueError), match="allowed_enums.aging_bucket.0"):
        _validate_contract_payload_strict(sanitized)


def test_ar_review_fixture_d1031516_passes_after_numeric_allowed_enum_normalization() -> None:
    if not _FIXTURE_D1031516.is_file():
        pytest.skip("d1031516 fixture unavailable")
    fixture = json.loads(_FIXTURE_D1031516.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["repair_contract"],
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    assert sanitized["allowed_enums"]["aging_bucket"] == ["30", "60", "90", "120"]
    contract = _validate_contract_payload_strict(sanitized)
    assert contract.allowed_enums["aging_bucket"] == ["30", "60", "90", "120"]
    assert contract.allowed_enums["current_status"] == ["pending", "paid", "overdue"]


def test_vendor_payment_fixture_reproduces_non_arithmetic_formula_failure_without_finalizer() -> None:
    if not _FIXTURE_VENDOR_PAYMENT_FORMULA.is_file():
        pytest.skip("vendor-payment formula fixture unavailable")
    fixture = json.loads(_FIXTURE_VENDOR_PAYMENT_FORMULA.read_text(encoding="utf-8"))
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(
            fixture["contract"],
            schema_profile=fixture["schema_profile"],
        )


def test_vendor_payment_fixture_passes_after_non_arithmetic_formula_finalization() -> None:
    if not _FIXTURE_VENDOR_PAYMENT_FORMULA.is_file():
        pytest.skip("vendor-payment formula fixture unavailable")
    fixture = json.loads(_FIXTURE_VENDOR_PAYMENT_FORMULA.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
    )
    calculated_by_name = {
        item["name"]: item
        for item in sanitized["calculated_fields"]
        if isinstance(item, dict)
    }
    semantics_by_name = {
        item["name"]: item
        for item in sanitized["output_column_semantics"]
        if isinstance(item, dict)
    }
    assert calculated_by_name["open_amount"]["formula"] == "invoice_amount - paid_amount"
    assert calculated_by_name["recommended_payment_date"]["formula"] is None
    assert semantics_by_name["recommended_payment_date"]["producer_kind"] == "other"
    assert "derived date value" in semantics_by_name["recommended_payment_date"]["row_semantics"]
    assert "date field is never left blank" in semantics_by_name["recommended_payment_date"][
        "fallback_value_semantics"
    ]
    contract = _validate_contract_payload_strict(
        sanitized,
        schema_profile=fixture["schema_profile"],
    )
    assert {field.name: field.formula for field in contract.calculated_fields}[
        "recommended_payment_date"
    ] is None


def test_numeric_arithmetic_formula_is_preserved_by_finalizer() -> None:
    if not _FIXTURE_VENDOR_PAYMENT_FORMULA.is_file():
        pytest.skip("vendor-payment formula fixture unavailable")
    fixture = json.loads(_FIXTURE_VENDOR_PAYMENT_FORMULA.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract"],
        schema_profile=fixture["schema_profile"],
    )
    calculated_by_name = {
        item["name"]: item
        for item in sanitized["calculated_fields"]
        if isinstance(item, dict)
    }
    assert calculated_by_name["open_amount"]["formula"] == "invoice_amount - paid_amount"


def test_ambiguous_unsupported_formula_remains_invalid_after_finalizer() -> None:
    payload = _minimal_contract_payload(
        input_columns=["invoice_id", "amount", "status"],
        output_columns=["invoice_id", "amount", "status", "decision_output"],
        required_output_columns=["decision_output"],
        output_column_semantics=[
            {
                "name": "decision_output",
                "description": "Derived decision output",
                "producer_kind": "other",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Populate the derived decision output for each row.",
                "fallback_value_semantics": "Use the workflow's explicit default so the value is never blank.",
            }
        ],
        calculated_fields=[
            {
                "name": "decision_output",
                "description": "Derived decision output",
                "formula": "choose_output(amount, status)",
            }
        ],
    )
    sanitized = _apply_live_contract_sanitizers(
        payload,
        schema_profile={"columns": ["invoice_id", "amount", "status"]},
    )
    assert sanitized["calculated_fields"][0]["formula"] == "choose_output(amount, status)"
    with pytest.raises(ContractSemanticValidationError) as exc_info:
        _validate_contract_payload_strict(
            sanitized,
            schema_profile={"columns": ["invoice_id", "amount", "status"]},
        )
    issues = _contract_validation_issue_details(exc_info.value)
    assert any(issue["type"] == "formula_syntax" for issue in issues)


def test_ar_review_fixture_9184aa74_rejects_unknown_review_input_columns_before_codegen() -> None:
    if not _FIXTURE_9184AA74.is_file():
        pytest.skip("9184aa74 fixture unavailable")
    fixture = json.loads(_FIXTURE_9184AA74.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["review_contract"],
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    with pytest.raises(ContractSemanticValidationError) as exc_info:
        _validate_contract_payload_strict(
            sanitized,
            schema_profile=fixture["schema_profile"],
            contract_reference=fixture["contract_reference"],
        )
    issues = _contract_validation_issue_details(exc_info.value)
    assert any(issue["type"] == "unknown_input_columns" for issue in issues)
    assert any(issue["type"] == "review_input_column_drift" for issue in issues)
    assert any("date_of_transaction" in issue.get("message", "") for issue in issues)


def test_reviewed_contract_preserving_uploaded_and_planned_columns_passes() -> None:
    if not _FIXTURE_9184AA74.is_file():
        pytest.skip("9184aa74 fixture unavailable")
    fixture = json.loads(_FIXTURE_9184AA74.read_text(encoding="utf-8"))
    sanitized = _apply_live_contract_sanitizers(
        fixture["contract_reference"],
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    contract = _validate_contract_payload_strict(
        sanitized,
        schema_profile=fixture["schema_profile"],
        contract_reference=fixture["contract_reference"],
    )
    assert set(contract.input_columns) == set(fixture["contract_reference"]["input_columns"])


def test_review_drift_replacing_planned_input_columns_is_rejected() -> None:
    if not _FIXTURE_9184AA74.is_file():
        pytest.skip("9184aa74 fixture unavailable")
    fixture = json.loads(_FIXTURE_9184AA74.read_text(encoding="utf-8"))
    drifted = dict(fixture["contract_reference"])
    drifted["input_columns"] = [
        "customer_id",
        "date_of_transaction",
        "amount",
    ]
    drifted["output_columns"] = list(drifted["input_columns"]) + ["aging_bucket"]
    drifted["required_output_columns"] = ["aging_bucket"]
    drifted["output_column_semantics"] = [
        {
            "name": "aging_bucket",
            "description": "Aging bucket",
            "producer_kind": "classification",
            "required": True,
            "nullable": False,
            "allow_empty_string": False,
            "row_semantics": "Assign aging bucket for each output row.",
            "fallback_value_semantics": "Use the workflow's explicit default or normalization rule so the derived value is never left blank.",
        }
    ]
    drifted["calculated_fields"] = []
    drifted["summary_group_keys"] = []
    drifted["summary_metrics"] = []
    drifted["exception_rules"] = []
    drifted["validation_checks"] = []
    with pytest.raises(ContractSemanticValidationError) as exc_info:
        _validate_contract_payload_strict(
            drifted,
            schema_profile=fixture["schema_profile"],
            contract_reference=fixture["contract_reference"],
        )
    issues = _contract_validation_issue_details(exc_info.value)
    drift_issue = next(issue for issue in issues if issue["type"] == "review_input_column_drift")
    assert drift_issue["added_columns"] == ["amount", "date_of_transaction"]
    assert "preserve the planned input column universe" in drift_issue["message"]


def test_alternate_dialect_maps_calculated_field_definitions_when_calculated_fields_missing() -> None:
    payload = _minimal_contract_payload(calculated_fields=[])
    payload.pop("calculated_fields", None)
    payload["calculated_field_definitions"] = [
        {"name": "residual_balance", "formula": "amount - paid_amount"},
    ]
    sanitized = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile={"columns": ["amount", "paid_amount"]},
    )
    assert sanitized["calculated_fields"][0]["name"] == "residual_balance"
    assert "calculated_field_definitions" not in sanitized


def test_alternate_dialect_keeps_existing_calculated_fields_when_both_aliases_present() -> None:
    payload = _minimal_contract_payload(
        calculated_fields=[{"name": "amount_due", "description": "Amount due", "formula": "amount"}],
    )
    payload["calculated_field_definitions"] = [
        {"name": "residual_balance", "formula": "amount - paid_amount"},
    ]
    sanitized = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile={"columns": ["amount"]},
    )
    assert sanitized["calculated_fields"][0]["name"] == "amount_due"
    assert "calculated_field_definitions" not in sanitized


def test_alternate_dialect_converts_output_column_semantics_dict_shape() -> None:
    payload = _minimal_contract_payload(
        output_column_semantics={
            "invoice_id": {
                "producer_kind": "copied_input",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Copied unchanged from the source row.",
                "fallback_value_semantics": "Carry through unchanged.",
                "description": "Invoice id",
            }
        },
    )
    sanitized = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile={"columns": ["invoice_id"]},
    )
    assert sanitized["output_column_semantics"][0]["name"] == "invoice_id"


def test_alternate_dialect_maps_row_level_output_path_when_row_level_output_file_missing() -> None:
    payload = _minimal_contract_payload()
    payload.pop("row_level_output_file", None)
    payload["row_level_output_path"] = "outputs/row_level_output.csv"
    sanitized = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile={"columns": ["invoice_id"]},
    )
    assert sanitized["row_level_output_file"] == "outputs/row_level_output.csv"
    assert "row_level_output_path" not in sanitized


def test_alternate_dialect_fills_workflow_type_from_contract_reference_only_when_missing() -> None:
    payload = _minimal_contract_payload()
    payload.pop("workflow_type", None)
    sanitized = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile={"columns": ["invoice_id"]},
        contract_reference={"workflow_type": "finance_exception_review"},
    )
    assert sanitized["workflow_type"] == "finance_exception_review"


def test_alternate_dialect_leaves_ambiguous_output_column_semantics_dict_invalid() -> None:
    payload = _minimal_contract_payload(
        required_output_columns=["derived_metric"],
        output_column_semantics={
            "derived_metric": {
                "producer_kind": "calculated_field",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
            }
        },
    )
    sanitized = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile={"columns": ["invoice_id"]},
    )
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def _review_meta_leakage_payload(**overrides: object) -> dict[str, object]:
    payload = _minimal_contract_payload(
        input_schema_path=None,
        output_schema_path=None,
        output_data_formats=["csv"],
        input_file_pattern=None,
        output_file_prefix="output",
        required_file_profile_fields={
            "input_file": "uploads/remittance_input.csv",
            "primary_key_columns": ["invoice_id"],
        },
        calculated_field_guidance={"formula_input_columns": []},
        exception_rule_guidance={"condition_formula": None},
        validation_check_guidance={"repair_rule": "do not leak"},
        allowed_enums_guidance={"planning_rules": []},
        artifact_path_namespace_guidance={"mapping_rules": []},
        formula_columns=[],
        preserve_original_data=True,
    )
    payload.pop("input_format", None)
    payload.pop("input_file", None)
    payload.update(overrides)
    return payload


def test_contract_prompt_rules_forbid_top_level_prompt_leakage_fields() -> None:
    rules = "\n".join(_contract_prompt_rules())
    assert "Return only AuthorOutputContract fields" in rules
    guidance = _contract_return_shape_guidance()
    assert "input_schema_path" in guidance["forbidden_top_level_fields"]
    assert "calculated_field_guidance" in guidance["forbidden_top_level_fields"]


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "input_schema_path",
        "output_schema_path",
        "output_data_formats",
        "input_file_pattern",
        "output_file_prefix",
        "calculated_field_guidance",
        "exception_rule_guidance",
        "validation_check_guidance",
        "allowed_enums_guidance",
        "artifact_path_namespace_guidance",
    ],
)
def test_sanitize_contract_shape_payload_strips_forbidden_top_level_meta_fields(
    forbidden_field: str,
) -> None:
    payload = _review_meta_leakage_payload(**{forbidden_field: {"leaked": True}})
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={
            "agent_input_path": "uploads/normalised_input.csv",
            "upload_format": "csv",
        },
    )
    assert changed is True
    assert forbidden_field not in sanitized


def test_sanitize_contract_shape_payload_populates_missing_input_format_from_xlsx_profile() -> None:
    payload = _review_meta_leakage_payload()
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={
            "agent_input_path": "uploads/normalised_input.csv",
            "upload_format": "xlsx",
            "selected_sheet": "AR_Remittance",
        },
    )
    assert changed is True
    assert sanitized["input_format"] == "xlsx"
    assert sanitized["input_file"] == "uploads/normalised_input.csv"


def test_sanitize_contract_shape_payload_populates_missing_input_format_from_csv_profile() -> None:
    payload = _review_meta_leakage_payload()
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={
            "agent_input_path": "uploads/input.csv",
            "upload_format": "csv",
        },
    )
    assert sanitized["input_format"] == "csv"


def test_sanitize_contract_shape_payload_populates_input_format_from_input_file_extension() -> None:
    payload = _review_meta_leakage_payload(input_file="uploads/workbook.xlsx")
    sanitized, _ = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"columns": ["invoice_id"]},
    )
    assert sanitized["input_format"] == "xlsx"


def test_sanitize_contract_shape_payload_leaves_missing_input_format_when_unambiguous() -> None:
    payload = _review_meta_leakage_payload()
    sanitized, _ = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"columns": ["invoice_id"]},
    )
    assert "input_format" not in sanitized
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_sanitize_contract_shape_payload_preserves_real_deliverables_and_output_files() -> None:
    payload = _review_meta_leakage_payload(
        row_level_output_file="outputs/output.csv",
        exception_output_files=["outputs/exceptions.csv"],
        summary_output_files=["outputs/summary_by_customer.csv"],
        requested_deliverables=[
            "outputs/output.csv",
            {
                "name": "exceptions",
                "output_path": "outputs/exceptions.csv",
                "required": True,
            },
        ],
        input_file="uploads/normalised_input.csv",
        input_format="csv",
    )
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={
            "agent_input_path": "uploads/normalised_input.csv",
            "upload_format": "csv",
        },
    )
    assert changed is True
    assert sanitized["row_level_output_file"] == "outputs/output.csv"
    assert sanitized["exception_output_files"] == ["outputs/exceptions.csv"]
    assert sanitized["summary_output_files"] == ["outputs/summary_by_customer.csv"]
    assert sanitized["requested_deliverables"][1]["output_path"] == "outputs/exceptions.csv"
    assert sanitized["input_file"] == "uploads/normalised_input.csv"


def test_sanitize_contract_shape_payload_strips_session_444_review_meta_leakage() -> None:
    review_path = (
        Path(__file__).resolve().parents[3]
        / ".workspaces/44487cf8-75e2-4521-9c3e-05ce0eb90472/generated/model_responses/contract_review.txt"
    )
    if not review_path.is_file():
        pytest.skip("session 44487cf8 review artifact unavailable")
    raw = review_path.read_text(encoding="utf-8")
    payload, _ = _extract_json_payload_with_hygiene(raw)
    contract = payload.get("reviewed_contract") or payload
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=dict(contract),
        schema_profile={
            "agent_input_path": "uploads/normalised_input.csv",
            "normalized_input_path": "uploads/normalised_input.csv",
            "upload_format": "xlsx",
            "selected_sheet": "AR_Remittance",
        },
    )
    assert changed is True
    for field in (
        "input_schema_path",
        "output_schema_path",
        "output_data_formats",
        "required_file_profile_fields",
        "calculated_field_guidance",
        "exception_rule_guidance",
    ):
        assert field not in sanitized
    assert sanitized["input_format"] == "xlsx"
    assert sanitized["input_file"] == "uploads/normalised_input.csv"


def test_sanitize_contract_shape_payload_does_not_rewrite_input_file_or_ambiguous_paths() -> None:
    payload = _minimal_contract_payload(
        input_file="uploads/normalised_input.csv",
        summary_output_files=["validation/evidence.json"],
        validation_checks=[
            {
                "check_id": "check_1",
                "layer": "row",
                "name": "Generated code path preserved",
                "required": True,
                "check_type": "non_null",
                "output_file": "generated/agent.py",
            }
        ],
    )
    sanitized, changed = _sanitize_contract_shape_payload(
        payload=payload,
        schema_profile={"agent_input_path": "uploads/normalised_input.csv"},
    )
    assert sanitized["input_file"] == "uploads/normalised_input.csv"
    assert sanitized["summary_output_files"] == ["validation/evidence.json"]
    assert sanitized["validation_checks"][0]["output_file"] == "generated/agent.py"
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(sanitized)


def test_json_hygiene_removes_inline_line_comment_outside_strings() -> None:
    payload, classes = _extract_json_payload_with_hygiene(
        '{"formula":"residual_balance" // Assuming GBP\n}'
    )
    assert payload == {"formula": "residual_balance"}
    assert "outside_string_line_comment_removal" in classes


def test_json_hygiene_preserves_double_slash_inside_strings_and_urls() -> None:
    text = '{"note":"keep // text","url":"https://example.com/path"}'
    payload, classes = _extract_json_payload_with_hygiene(text)
    assert payload == {
        "note": "keep // text",
        "url": "https://example.com/path",
    }
    assert classes == []


def test_json_hygiene_preserves_block_comment_text_inside_strings() -> None:
    text = '{"note":"keep /* comment */ text","formula":"gross /* literal */"}'
    payload, classes = _extract_json_payload_with_hygiene(text)
    assert payload == {
        "note": "keep /* comment */ text",
        "formula": "gross /* literal */",
    }
    assert classes == []


def test_json_hygiene_accepts_markdown_fenced_json() -> None:
    payload, classes = _extract_json_payload_with_hygiene(
        "```json\n{\"workflow_type\":\"x\"}\n```"
    )
    assert payload == {"workflow_type": "x"}
    assert "markdown_fence_stripping" in classes


def test_json_hygiene_removes_block_comments_outside_strings() -> None:
    payload, classes = _extract_json_payload_with_hygiene(
        '{"a": 1 /* remove me */, "b": 2}'
    )
    assert payload == {"a": 1, "b": 2}
    assert "outside_string_block_comment_removal" in classes


def test_json_hygiene_removes_safe_trailing_commas() -> None:
    payload, classes = _extract_json_payload_with_hygiene(
        '{"a": [1, 2,], "b": {"c": 3,},}'
    )
    assert payload == {"a": [1, 2], "b": {"c": 3}}
    assert "trailing_comma_removal" in classes


def test_json_hygiene_returns_none_for_unrecoverable_json() -> None:
    payload, classes = _extract_json_payload_with_hygiene('{"a":[1,,2]}')
    assert payload is None
    assert classes == []


def test_json_hygiene_does_not_add_missing_fields() -> None:
    payload, _classes = _extract_json_payload_with_hygiene('{"workflow_type":"x",}')
    assert payload == {"workflow_type": "x"}
    assert set(payload) == {"workflow_type"}


def test_json_hygiene_does_not_mutate_formula_strings() -> None:
    text = (
        '{"formula":"https://example.com/path?a=1//2 and note == \\"/* keep */\\"",'
        '"other":"value"}'
    )
    payload, _classes = _extract_json_payload_with_hygiene(text)
    assert payload is not None
    assert payload["formula"] == 'https://example.com/path?a=1//2 and note == "/* keep */"'


def test_schema_invalid_json_still_fails_strict_validation_after_hygiene() -> None:
    payload, classes = _extract_json_payload_with_hygiene(
        '{"author_output_contract":{"workflow_type":"x"} // comment\n}'
    )
    assert payload is not None
    assert "outside_string_line_comment_removal" in classes
    with pytest.raises((ValidationError, ValueError)):
        _validate_contract_payload_strict(payload["author_output_contract"])


def test_ar_remittance_style_inline_comment_reaches_strict_contract_validation() -> None:
    raw = """```json
{
  "author_output_contract": {
    "workflow_type": "ar_remittance_workbook",
    "workflow_confidence": 0.9,
    "build_mode": "llm_custom",
    "input_file": "uploads/normalised_input.csv",
    "input_format": "xlsx",
    "selected_sheet": "AR_Remittance",
    "row_level_output_file": "outputs/output.csv",
    "summary_output_files": [
      {
        "path": "outputs/summary_by_customer_and_aging_bucket.csv",
        "description": "Summary of exposure by customer and aging bucket",
        "required_columns": ["customer_id", "aging_bucket", "total_exposure_gbp"]
      }
    ],
    "exception_output_files": [
      {
        "path": "outputs/investigation_required.csv",
        "description": "Invoices that need investigation",
        "required_columns": ["invoice_id", "issue_flag", "issue_reason"]
      }
    ],
    "primary_row_key": "invoice_id",
    "required_output_columns": ["residual_balance", "exposure_gbp", "aging_bucket"],
    "optional_output_columns": [],
    "output_column_semantics": [
      {
        "name": "residual_balance",
        "description": "Calculated residual balance after deductions and credits",
        "producer_kind": "calculated_field",
        "required": true,
        "nullable": false,
        "allow_empty_string": false,
        "row_semantics": "Emit the calculated residual balance for each invoice.",
        "fallback_value_semantics": "0.0"
      },
      {
        "name": "exposure_gbp",
        "description": "Open exposure converted to GBP",
        "producer_kind": "calculated_field",
        "required": true,
        "nullable": false,
        "allow_empty_string": false,
        "row_semantics": "Emit the open exposure in GBP for each invoice.",
        "fallback_value_semantics": "0.0"
      },
      {
        "name": "aging_bucket",
        "description": "Aging bucket classification as of 2026-05-01",
        "producer_kind": "calculated_field",
        "required": true,
        "nullable": false,
        "allow_empty_string": false,
        "row_semantics": "Emit the aging bucket classification for each invoice.",
        "fallback_value_semantics": "Unknown"
      }
    ],
    "calculated_fields": [
      {
        "name": "residual_balance",
        "description": "Residual balance calculation after deductions and credits",
        "formula": "paid_amount - (invoice_amount + credit_note_amount + deduction_amount)"
      },
      {
        "name": "exposure_gbp",
        "description": "Convert open exposure into GBP using FX rates",
        "formula": "residual_balance" // Assuming residual balance is already in GBP
      }
    ],
    "formula_input_columns": [],
    "formula_output_columns": ["residual_balance", "exposure_gbp"],
    "tolerances": {},
    "summary_group_keys": ["customer_id", "aging_bucket"],
    "summary_metrics": [
      {
        "name": "total_exposure_gbp",
        "metric_type": "sum",
        "source_column": "exposure_gbp",
        "description": "Total exposure in GBP grouped by customer and aging bucket",
        "input_columns": ["exposure_gbp"],
        "output_column": "total_exposure_gbp"
      }
    ],
    "exception_rules": [
      {
        "name": "invoice_investigation_required",
        "condition": "dispute_code != 'NONE' OR deduction_reason != 'none'",
        "reason": "Invoice requires investigation due to dispute or special deductions.",
        "severity": "high",
        "output_column": "issue_flag"
      }
    ],
    "allowed_enums": {
      "issue_flag": ["no_issue", "review_required"]
    },
    "validation_checks": [
      {
        "check_id": "row_count_preserved",
        "layer": "row_level",
        "name": "Row count preserved",
        "required": true,
        "check_type": "row_count"
      }
    ],
    "skipped_checks": [],
    "clarification_questions": [],
    "unsupported_reasons": []
  }
}
```"""
    payload, classes = _extract_json_payload_with_hygiene(raw)
    assert payload is not None
    assert "markdown_fence_stripping" in classes
    assert "outside_string_line_comment_removal" in classes
    contract = _validate_contract_payload_strict(payload["author_output_contract"])
    assert contract.workflow_type == "ar_remittance_workbook"
    assert contract.selected_sheet == "AR_Remittance"
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "outputs/summary_by_customer_and_aging_bucket.csv",
        "outputs/investigation_required.csv",
    ]


def test_missing_deliverables_accepts_deliverable_spec_objects() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            missing_deliverables=[
                DeliverableSpec(
                    name="validation_report",
                    description="Not yet produced",
                    output_path="reports/validation_report.md",
                    required=True,
                    status="pending",
                )
            ],
        )
    )
    missing = contract.missing_deliverables[0]
    assert isinstance(missing, DeliverableSpec)
    assert missing.status == "pending"


def test_invalid_deliverable_object_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthorOutputContract.model_validate(
            _minimal_contract_payload(
                requested_deliverables=[
                    {
                        "description": "Missing required name field",
                    }
                ]
            )
        )


def test_reviewed_contract_with_richer_structures_passes_validation(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    reviewed_contract = dict(_payload(script[0])["author_output_contract"])
    reviewed_contract["summary_metrics"] = [
        {
            "name": "category_counts",
            "metric_type": "count",
            "source_column": "category",
            "group_by": ["category"],
            "description": "Count transactions by category",
            "input_columns": ["category"],
            "output_column": "category_count",
        }
    ]
    reviewed_contract["requested_deliverables"] = [
        "outputs/output.csv",
        {
            "name": "validation_report",
            "description": "Validation report explaining rules and counts",
            "output_path": "reports/validation_report.md",
            "required": True,
        },
    ]
    review = _response(
        "richer-reviewed-contract",
        {"reviewed_contract": reviewed_contract, "approved": True},
    )
    result, _event_log, _sid, workspace = _run_pipeline(
        workspaces_root,
        [script[0], review, script[2], script[3]],
    )
    assert result.ok is True
    saved = json.loads(
        (workspace / "generated" / "author_output_contract.json").read_text(encoding="utf-8")
    )
    assert saved["summary_metrics"][0]["metric_type"] == "count"
    assert saved["summary_metrics"][0]["group_by"] == ["category"]
    assert saved["requested_deliverables"][1]["name"] == "validation_report"


def test_contract_planning_inline_comment_is_cleaned_before_json_repair(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    commented_plan_text = script[0].text.replace(
        '"build_mode": "llm_custom",',
        '"build_mode": "llm_custom" // formatting-only note\n,',
        1,
    )
    commented_plan = _response("commented-contract-plan", commented_plan_text)
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [commented_plan, script[1], script[2], script[3]],
    )
    assert result.ok is True
    assert "json_repair" not in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["build_mode"] == "llm_custom"


def test_summary_output_files_accepts_reports_validation_report_path() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            summary_output_files=[
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report explaining rules and counts",
                    "required_columns": ["category_counts"],
                }
            ],
        )
    )
    assert output_file_path(contract.summary_output_files[0]) == "reports/validation_report.md"
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    _validate_contract_payload_strict(contract.model_dump(mode="json"))


def test_structured_exception_output_file_spec_is_accepted() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            exception_output_files=[
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows requiring review",
                    "required_columns": ["invoice_id", "issue_reason"],
                }
            ]
        )
    )
    assert output_file_path(contract.exception_output_files[0]) == "outputs/exceptions.csv"


def test_structured_exception_rule_spec_is_accepted() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            exception_rules=[
                ExceptionRuleSpec(
                    name="short_paid",
                    condition="paid_amount < invoice_amount",
                    reason="Residual balance remains",
                    severity="medium",
                    output_column="issue_flag",
                )
            ]
        )
    )
    rule = contract.exception_rules[0]
    assert isinstance(rule, ExceptionRuleSpec)
    assert rule.name == "short_paid"


def test_formula_validation_check_spec_is_accepted() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            validation_checks=[
                ValidationCheckSpec(
                    check_id="net_amount_formula",
                    layer="business_rules",
                    name="Net amount formula",
                    check_type="formula",
                    formula="net_amount == paid_amount - fee_amount",
                    input_columns=["paid_amount", "fee_amount"],
                    output_column="net_amount",
                    tolerance=0.01,
                )
            ]
        )
    )
    check = contract.validation_checks[0]
    assert check.formula == "net_amount == paid_amount - fee_amount"


def test_contract_prompt_rules_forbid_contradictory_hard_validation_checks() -> None:
    rules = "\n".join(_contract_prompt_rules())
    assert "validation_checks are hard gates when required=true" in rules
    assert "contradicts the user prompt, allowed_enums" in rules
    assert "put that list in allowed_enums" in rules
    assert "must not reject that same value" in rules
    assert "advisory/reporting requirements" in rules
    assert "Do not replace prompt-provided category labels" in rules


def test_strict_contract_validation_rejects_check_that_forbids_allowed_enum() -> None:
    payload = _minimal_contract_payload(
        output_columns=["invoice_id", "amount", "category"],
        required_output_columns=["category"],
        output_column_semantics=[
            {
                "name": "category",
                "description": "Assigned label",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one allowed label for every row.",
                "fallback_value_semantics": "Uncategorised",
            }
        ],
        allowed_enums={
            "category": ["Income", "Office Expense", "Uncategorised"],
        },
        validation_checks=[
            {
                "check_id": "category_required",
                "layer": "business_rules",
                "name": "Category is required",
                "required": True,
                "check_type": "formula",
                "formula": "category != 'Uncategorised'",
                "input_columns": ["category"],
                "output_column": "category",
            }
        ],
    )

    with pytest.raises(ValueError, match="forbids category='Uncategorised'"):
        _validate_contract_payload_strict(payload)


def test_strict_contract_validation_rejects_check_that_forbids_fallback_value() -> None:
    payload = _minimal_contract_payload(
        output_columns=["invoice_id", "amount", "category"],
        required_output_columns=["category"],
        output_column_semantics=[
            {
                "name": "category",
                "description": "Assigned label",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one label for every row.",
                "fallback_value_semantics": "Uncategorised",
            }
        ],
        validation_checks=[
            {
                "check_id": "category_required",
                "layer": "business_rules",
                "name": "Category is required",
                "required": True,
                "check_type": "formula",
                "formula": "category != 'Uncategorised'",
                "input_columns": ["category"],
                "output_column": "category",
            }
        ],
    )

    with pytest.raises(ValueError, match="fallback_value_semantics"):
        _validate_contract_payload_strict(payload)


def test_strict_contract_validation_allows_reporting_fallback_without_hard_exclusion() -> None:
    payload = _minimal_contract_payload(
        output_columns=["invoice_id", "amount", "category"],
        required_output_columns=["category"],
        output_column_semantics=[
            {
                "name": "category",
                "description": "Assigned label",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one allowed label for every row.",
                "fallback_value_semantics": "Uncategorised",
            }
        ],
        allowed_enums={
            "category": ["Income", "Office Expense", "Uncategorised"],
        },
        validation_checks=[
            {
                "check_id": "category_membership",
                "layer": "business_rules",
                "name": "Category is one allowed value",
                "required": True,
                "check_type": "formula",
                "formula": "category in ['Income', 'Office Expense', 'Uncategorised']",
                "input_columns": ["category"],
                "output_column": "category",
            }
        ],
        summary_metrics=[
            {
                "name": "uncategorised_count",
                "metric_type": "count",
                "source_column": "category",
                "filter": "category == 'Uncategorised'",
                "description": "Count fallback rows for review reporting",
                "input_columns": ["category"],
                "output_column": "uncategorised_count",
            }
        ],
    )

    contract = _validate_contract_payload_strict(payload)
    assert contract.allowed_enums["category"][-1] == "Uncategorised"
    assert contract.summary_metrics[0].filter == "category == 'Uncategorised'"


def test_sanitize_prompt_explicit_category_enums_rewrites_allowed_enums_and_formula() -> None:
    payload = _minimal_contract_payload(
        input_columns=["transaction_id", "amount", "description"],
        output_columns=["transaction_id", "amount", "category"],
        required_output_columns=["category"],
        output_column_semantics=[
            {
                "name": "category",
                "description": "Assigned category",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Assign one category.",
                "fallback_value_semantics": "Other",
            }
        ],
        allowed_enums={"category": ["Stripe", "Bank", "Payment", "Transfer", "Other"]},
        validation_checks=[
            {
                "check_id": "valid_category_assignment",
                "layer": "business_rules",
                "name": "Valid category assignment",
                "required": True,
                "check_type": "formula",
                "formula": "category in ['Stripe', 'Bank', 'Payment', 'Transfer', 'Other']",
                "input_columns": ["category"],
                "output_column": "category",
            }
        ],
    )

    sanitized = _sanitize_prompt_explicit_category_enum_payload(
        payload,
        user_description=(
            "Assign each transaction to exactly one of:\n"
            "Revenue, Payroll, Software, Bank Fees, Travel, Rent, Tax, Office Supplies, Other.\n\n"
            "Use these categorisation rules."
        ),
    )

    assert sanitized["allowed_enums"]["category"] == [
        "Revenue",
        "Payroll",
        "Software",
        "Bank Fees",
        "Travel",
        "Rent",
        "Tax",
        "Office Supplies",
        "Other",
    ]
    assert sanitized["validation_checks"][0]["formula"] == (
        "category in ['Revenue', 'Payroll', 'Software', 'Bank Fees', 'Travel', 'Rent', 'Tax', 'Office Supplies', 'Other']"
    )


def test_strict_contract_validation_rejects_prompt_explicit_category_enum_drift() -> None:
    payload = _minimal_contract_payload(
        input_columns=["transaction_id", "amount", "description"],
        output_columns=["transaction_id", "amount", "category"],
        required_output_columns=["category"],
        output_column_semantics=[
            {
                "name": "category",
                "description": "Assigned category",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Assign one category.",
                "fallback_value_semantics": "Other",
            }
        ],
        allowed_enums={"category": ["Stripe", "Bank", "Payment", "Transfer", "Other"]},
        validation_checks=[
            {
                "check_id": "valid_category_assignment",
                "layer": "business_rules",
                "name": "Valid category assignment",
                "required": True,
                "check_type": "formula",
                "formula": "category in ['Stripe', 'Bank', 'Payment', 'Transfer', 'Other']",
                "input_columns": ["category"],
                "output_column": "category",
            }
        ],
    )

    with pytest.raises(ValueError, match="prompt-provided labels"):
        _validate_contract_payload_strict(
            payload,
            user_description=(
                "Assign each transaction to exactly one of:\n"
                "Revenue, Payroll, Software, Bank Fees, Travel, Rent, Tax, Office Supplies, Other.\n\n"
                "Use these categorisation rules."
            ),
        )


def test_output_column_semantics_spec_is_accepted() -> None:
    contract = AuthorOutputContract.model_validate(
        _minimal_contract_payload(
            output_column_semantics=[
                OutputColumnSemanticsSpec(
                    name="issue_flag",
                    description="Non-empty review flag",
                    producer_kind="exception_flag",
                    required=True,
                    nullable=False,
                    allow_empty_string=False,
                    row_semantics="Emit a non-empty review flag for every output row.",
                    fallback_value_semantics="Use an explicit default flag when no exception rule matches.",
                )
            ]
        )
    )
    spec = contract.output_column_semantics[0]
    assert spec.name == "issue_flag"
    assert spec.allow_empty_string is False


def test_validate_contract_payload_strict_rejects_unsupported_call_formula() -> None:
    payload = _minimal_contract_payload(
        output_columns=["invoice_id", "amount", "issue_flag", "category"],
        required_output_columns=["issue_flag", "category"],
        output_column_semantics=[
            {
                "name": "issue_flag",
                "description": "Non-empty review flag",
                "producer_kind": "exception_flag",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit a non-empty review flag for every output row.",
                "fallback_value_semantics": "Use an explicit default flag when no exception rule matches.",
            },
            {
                "name": "category",
                "description": "Derived category label",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit a non-empty category for every row.",
                "fallback_value_semantics": "Use an explicit fallback category when no rule matches.",
            },
        ],
        calculated_fields=[
            {
                "name": "category",
                "description": "Derived category label",
                "formula": "categorize_transaction(description, counterparty)",
            }
        ],
    )
    with pytest.raises(ValueError, match="non-arithmetic field 'category'"):
        _validate_contract_payload_strict(payload)


def test_validate_contract_payload_strict_requires_semantics_for_required_output_columns() -> None:
    payload = _minimal_contract_payload(output_column_semantics=[])
    with pytest.raises(ValueError, match="required_output_columns need explicit non-null row semantics"):
        _validate_contract_payload_strict(payload)


def test_near_miss_calculated_field_key_is_rejected_without_mapping() -> None:
    payload = _minimal_contract_payload(
        calculated_fields=[
            {
                "field_name": "net_amount",
                "description": "near-miss key",
                "formula": "amount",
            }
        ]
    )
    with pytest.raises(ValidationError):
        AuthorOutputContract.model_validate(payload)


def test_contract_planning_failure_user_message_hides_pydantic_noise() -> None:
    result = _contract_planning_failed_result(
        technical_detail="2 validation errors for AuthorOutputContract\nexception_rules.0",
        stages=["contract_planning"],
    )
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    assert "validation errors for AuthorOutputContract" not in (result.message or "")
    assert "validation errors for AuthorOutputContract" in (result.technical_detail or "")


def test_prompt_file_mismatch_returns_clarification_not_planning_failure(
    workspaces_root,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    clarification_question = (
        "The uploaded workbook looks like AR remittance/invoice exception review data, "
        "but the prompt describes payment processor export review. Should I treat this "
        "as AR remittance exception review, or upload a payment processor export?"
    )
    response = ModelResponse(
        id="clarification-plan",
        content=[
            TextBlock(
                text=json.dumps(
                    {
                        "author_output_contract": {
                            "workflow_type": "needs_clarification",
                            "build_mode": "clarification",
                            "clarification_questions": [
                                {
                                    "question": clarification_question,
                                    "reason": "Prompt and upload describe different finance domains.",
                                }
                            ],
                        },
                        "planning_notes": [],
                    }
                )
            )
        ],
        stop_reason="end_turn",
    )
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_clarification",
            model_client=FakeModelClient(script=[response]),
            workspace=workspace,
            user_description="Review this payment processor export.",
            schema_profile={
                "columns": ["invoice_id", "remittance_id", "paid_amount"],
                "row_count": 3,
                "upload_format": "xlsx",
                "agent_input_path": "uploads/remittance.xlsx",
            },
            reference_scaffold_root=None,
            workflow_type="payment_processor_reconciliation",
        )
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_CLARIFICATION_REQUIRED
    assert clarification_question in (result.message or "")
    assert "validation errors for AuthorOutputContract" not in (result.message or "")


def test_top_level_skeleton_includes_profiled_input_format() -> None:
    skeleton = _contract_top_level_skeleton(
        schema_profile={
            "upload_format": "xlsx",
            "agent_input_path": "uploads/workbook.xlsx",
            "selected_sheet": "remittance",
            "columns": ["invoice_id"],
        },
        workflow_type="finance_exception_review",
    )
    assert skeleton["input_format"] == "xlsx"
    assert skeleton["input_file"] == "uploads/workbook.xlsx"
    assert skeleton["selected_sheet"] == "remittance"
    assert skeleton["build_mode"] == "llm_custom"


def test_invalid_build_mode_triggers_schema_repair_not_backend_mapping(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["build_mode"] = "model_authored_finance_workflow"
    invalid_plan = _response(
        "invalid-build-mode-plan",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )
    repaired_contract = dict(_payload(script[0])["author_output_contract"])
    repair = _response(
        "invalid-build-mode-repair",
        {"author_output_contract": repaired_contract, "planning_notes": []},
    )
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [invalid_plan, repair, script[1], script[2], script[3]],
    )
    assert result.ok is True
    assert "contract_schema_repair" in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["build_mode"] == "llm_custom"
    assert "model_authored_finance_workflow" not in json.dumps(saved)


def test_missing_input_format_is_populated_from_profile_without_schema_repair(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract.pop("input_format", None)
    invalid_plan = _response(
        "missing-input-format-plan",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [invalid_plan, script[1], script[2], script[3]],
    )
    assert result.ok is True
    assert "contract_schema_repair" not in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["input_format"] == "csv"


def test_unsupported_calculated_field_formula_triggers_schema_repair_to_row_semantics(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["calculated_fields"] = [
        {
            "name": "category",
            "description": "Derived category label",
            "formula": "categorize_transaction(description, counterparty)",
        }
    ]
    invalid_plan = _response(
        "unsupported-formula-plan",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [invalid_plan, script[1], script[2], script[3]],
    )

    assert result.ok is True
    assert "contract_schema_repair" not in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["calculated_fields"] == [
        {
            "name": "category",
            "description": "Derived category label",
            "formula": None,
        }
    ]
    semantics = {entry["name"]: entry for entry in saved["output_column_semantics"]}
    assert "category" in semantics
    assert semantics["category"]["row_semantics"]
    assert semantics["category"]["fallback_value_semantics"]


def test_invalid_build_mode_after_repair_fails_honestly(workspaces_root: Path) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["build_mode"] = "model_authored_finance_workflow"
    invalid_plan = _response(
        "invalid-build-mode-plan",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )
    still_invalid = _response(
        "invalid-build-mode-repair",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )
    result, event_log, sid, _workspace = _run_pipeline(
        workspaces_root,
        [invalid_plan, still_invalid],
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    assert "contract_schema_repair" in _purposes(event_log, sid)
    assert "validation errors for AuthorOutputContract" not in (result.message or "")
    assert result.technical_detail


def test_build_mode_clarification_requests_user_clarification(workspaces_root: Path) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    question = "Should this run treat the upload as invoice exception review or payment export review?"
    response = _response(
        "clarification-build-mode",
        {
            "author_output_contract": {
                "workflow_type": "needs_clarification",
                "build_mode": "clarification",
                "input_file": "uploads/sample_input.csv",
                "input_format": "csv",
                "clarification_questions": [
                    {
                        "question": question,
                        "reason": "Prompt and upload describe different finance workflows.",
                    }
                ],
            },
            "planning_notes": [],
        },
    )
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_clarification_mode",
            model_client=FakeModelClient(script=[response]),
            workspace=workspace,
            user_description="Review payment processor export.",
            schema_profile={
                "columns": ["invoice_id"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="payment_processor_reconciliation",
        )
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_CLARIFICATION_REQUIRED
    assert question in (result.message or "")
    assert not (workspace / "generated" / "agent.py").exists()


def test_contract_review_accepts_structured_summary_metrics(workspaces_root: Path) -> None:
    script = _base_script()
    reviewed_contract = dict(_payload(script[0])["author_output_contract"])
    reviewed_contract["summary_group_keys"] = ["account"]
    reviewed_contract["summary_metrics"] = [
        {
            "name": "amount_total",
            "metric_type": "sum",
            "source_column": "amount",
            "description": "Total amount by account",
        }
    ]
    review = _response(
        "structured-summary-metrics-review",
        {"reviewed_contract": reviewed_contract, "approved": True},
    )
    result, _event_log, _sid, workspace = _run_pipeline(
        workspaces_root,
        [script[0], review, script[2], script[3]],
    )
    assert result.ok is True
    saved = json.loads(
        (workspace / "generated" / "author_output_contract.json").read_text(encoding="utf-8")
    )
    metric = saved["summary_metrics"][0]
    assert metric["name"] == "amount_total"
    assert metric["metric_type"] == "sum"


def test_summary_metrics_condition_triggers_schema_repair_with_allowed_fields_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "condition": "confidence < 0.8",
            "description": "Count rows with low confidence",
        }
    ]
    corrected_contract = dict(_payload(script[0])["author_output_contract"])
    corrected_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "source_column": "confidence",
            "filter": "confidence < 0.8",
            "description": "Count rows with low confidence",
            "input_columns": ["confidence"],
            "output_column": "uncertain_rows_count",
        }
    ]
    model = FakeModelClient(
        script=[
            _response(
                "summary-metric-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "summary-metric-schema-repair",
                {"author_output_contract": corrected_contract, "planning_notes": []},
            ),
            script[1],
            script[2],
            script[3],
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_summary_metric_schema_repair",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[1]["messages"][0]["content"][0]["text"])
    assert prompt["summary_metric_guidance"]["allowed_fields"] == [
        "name",
        "metric_type",
        "source_column",
        "group_by",
        "filter",
        "description",
        "formula",
        "input_columns",
        "output_column",
    ]
    assert prompt["summary_metric_guidance"]["forbidden_fields"] == ["condition"]
    assert any(
        issue.get("path") == "summary_metrics[0].condition"
        and issue.get("forbidden_field") == "condition"
        for issue in prompt["validation_issue_details"]
    )
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    metric = saved["summary_metrics"][0]
    assert metric["filter"] == "confidence < 0.8"
    assert "condition" not in metric


def test_invalid_summary_metrics_do_not_reach_codegen_when_schema_repair_stays_invalid(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "condition": "confidence < 0.8",
        }
    ]
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [
            _response(
                "summary-metric-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "summary-metric-invalid-repair",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
        ],
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    assert "contract_schema_repair" in _purposes(event_log, sid)
    assert "code_generation" not in _purposes(event_log, sid)
    assert not (workspace / "generated" / "agent.py").exists()


def test_contradictory_validation_check_triggers_schema_repair_with_consistency_context(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["allowed_enums"] = {
        "category": ["Income", "Office Expense", "Uncategorised"],
    }
    invalid_contract["output_column_semantics"] = [
        {
            "name": "category",
            "description": "Assigned transaction category",
            "producer_kind": "classification",
            "required": True,
            "nullable": False,
            "allow_empty_string": False,
            "row_semantics": "Emit one allowed category for every row.",
            "fallback_value_semantics": "Uncategorised",
        },
        *[
            item
            for item in invalid_contract.get("output_column_semantics", [])
            if item.get("name") != "category"
        ],
    ]
    invalid_contract["validation_checks"] = [
        {
            "check_id": "category_required",
            "layer": "business_rules",
            "name": "Category is required",
            "required": True,
            "check_type": "formula",
            "formula": "category != 'Uncategorised'",
            "input_columns": ["category"],
            "output_column": "category",
        }
    ]
    corrected_contract = dict(invalid_contract)
    corrected_contract["validation_checks"] = [
        {
            "check_id": "category_allowed",
            "layer": "business_rules",
            "name": "Category is an allowed value",
            "required": True,
            "check_type": "formula",
            "formula": "category in ['Income', 'Office Expense', 'Uncategorised']",
            "input_columns": ["category"],
            "output_column": "category",
        }
    ]
    model = FakeModelClient(
        script=[
            _response(
                "contradictory-validation-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "contradictory-validation-schema-repair",
                {"author_output_contract": corrected_contract, "planning_notes": []},
            ),
            _response(
                "contradictory-validation-review",
                {"reviewed_contract": corrected_contract, "approved": True},
            ),
            script[2],
            script[3],
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_contradictory_validation_repair",
            model_client=model,
            workspace=workspace,
            user_description=(
                "Categorise rows as Income, Office Expense, or Uncategorised and "
                "report uncertain rows."
            ),
            schema_profile={
                "columns": [
                    "txn_id",
                    "date",
                    "amount",
                    "description",
                    "counterparty",
                    "account",
                ],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[1]["messages"][0]["content"][0]["text"])
    assert any(
        issue.get("path") == "validation_checks[0].formula"
        and issue.get("type") == "contract_consistency"
        and issue.get("forbidden_value") == "Uncategorised"
        for issue in prompt["validation_issue_details"]
    )
    assert "allowed_enums or fallback_value_semantics" in prompt["instruction"]
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["validation_checks"][0]["formula"] == (
        "category in ['Income', 'Office Expense', 'Uncategorised']"
    )


def _nested_validation_check_contract_payload() -> dict[str, object]:
    return _minimal_contract_payload(
        workflow_type="model_authored_finance_workflow",
        input_columns=["txn_id", "date", "amount", "description", "counterparty", "account"],
        output_columns=[
            "txn_id",
            "date",
            "amount",
            "description",
            "counterparty",
            "account",
            "category",
            "rule_matched",
            "rule_used",
            "confidence",
        ],
        required_output_columns=["category", "rule_matched", "rule_used", "confidence"],
        output_column_semantics=[
            {
                "name": "category",
                "description": "Assigned category",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one category for every row.",
                "fallback_value_semantics": "Uncategorised",
            },
            {
                "name": "rule_matched",
                "description": "Matched rule",
                "producer_kind": "rule_explanation",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one rule explanation for every row.",
                "fallback_value_semantics": "No Match",
            },
            {
                "name": "rule_used",
                "description": "Rule label",
                "producer_kind": "rule_explanation",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one rule label for every row.",
                "fallback_value_semantics": "No Rule",
            },
            {
                "name": "confidence",
                "description": "Confidence score",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit one confidence value for every row.",
                "fallback_value_semantics": "0.0",
            },
        ],
        validation_checks=[
            {
                "check_id": "row_count",
                "layer": "row_level",
                "name": "Row count preserved",
                "required": True,
                "check_type": "row_count",
            },
            {
                "check_id": "category_allowed_values",
                "layer": "business_rules",
                "name": "Category values within allowed list",
                "required": True,
                "check_type": "enum",
                "output_column": "category",
                "input_columns": ["category"],
                "allowed_enums": {
                    "category": [
                        "Income",
                        "Office Expense",
                        "Travel",
                        "Subscriptions",
                        "Refund",
                        "Uncategorised",
                    ],
                },
            },
            {
                "check_id": "confidence_range",
                "layer": "business_rules",
                "name": "Confidence score within valid range",
                "required": True,
                "check_type": "range",
                "output_column": "confidence",
                "input_columns": ["confidence"],
                "min_value": 0.0,
                "max_value": 1.0,
            },
        ],
        allowed_enums={
            "category": [
                "Income",
                "Office Expense",
                "Travel",
                "Subscriptions",
                "Refund",
                "Uncategorised",
            ],
        },
    )


def test_strict_contract_validation_rejects_validation_checks_allowed_enums() -> None:
    payload = _nested_validation_check_contract_payload()
    with pytest.raises(ValidationError, match="validation_checks.1.allowed_enums"):
        AuthorOutputContract.model_validate(payload)


def test_strict_contract_validation_rejects_validation_checks_min_max_value() -> None:
    payload = _nested_validation_check_contract_payload()
    with pytest.raises(ValidationError, match="validation_checks.2.min_value"):
        AuthorOutputContract.model_validate(payload)


def test_contract_prompt_rules_forbid_nested_validation_check_fields() -> None:
    rules = "\n".join(_contract_prompt_rules())
    guidance = _validation_check_guidance()
    assert "Never put allowed_enums, allowed_values, categories, min_value, max_value" in rules
    assert "top-level AuthorOutputContract.allowed_enums" in rules
    assert "allowed_enums" in guidance["forbidden_nested_fields"]
    assert "allowed_values" in guidance["forbidden_nested_fields"]
    assert "categories" in guidance["forbidden_nested_fields"]
    assert "min_value" in guidance["forbidden_nested_fields"]
    assert "max_value" in guidance["forbidden_nested_fields"]
    assert guidance["valid_examples"]["top_level_allowed_enums"]["allowed_enums"]["category"]
    assert "preserve_original_data, enable_logging, or enable_debugging" in rules


def test_calculated_field_and_exception_rule_guidance_match_schema() -> None:
    calc = _calculated_field_guidance()
    exc = _exception_rule_guidance()
    assert calc["allowed_fields"] == ["name", "description", "formula"]
    assert calc["required_fields"] == ["name", "description"]
    assert "description is missing" in calc["repair_rule"]
    assert exc["allowed_fields"] == ["name", "condition", "reason", "severity", "output_column"]
    assert exc["forbidden_fields"] == ["issue_flag"]
    assert "output_column" in exc["repair_rule"]


def test_sanitize_validation_checks_preserves_top_level_allowed_enums() -> None:
    payload = _nested_validation_check_contract_payload()
    sanitized = _sanitize_validation_checks_payload(payload)
    contract = _validate_contract_payload_strict(sanitized)
    assert contract.allowed_enums["category"] == payload["allowed_enums"]["category"]
    assert all(
        "allowed_enums" not in check.model_dump()
        for check in contract.validation_checks
    )
    check_ids = {check.check_id for check in contract.validation_checks}
    assert "category_allowed_values" not in check_ids
    assert "confidence_range" in check_ids


def test_sanitize_validation_checks_converts_range_to_formula() -> None:
    payload = _nested_validation_check_contract_payload()
    sanitized = _sanitize_validation_checks_payload(payload)
    range_check = next(
        check
        for check in sanitized["validation_checks"]
        if check["check_id"] == "confidence_range"
    )
    assert range_check["check_type"] == "formula"
    assert range_check["formula"] == "0.0 <= confidence and confidence <= 1.0"
    assert "min_value" not in range_check
    assert "max_value" not in range_check


def test_sanitize_validation_checks_moves_allowed_values_to_top_level_allowed_enums() -> None:
    payload = _minimal_contract_payload(
        input_columns=["invoice_id", "currency"],
        output_columns=["invoice_id", "currency", "issue_flag"],
        validation_checks=[
            {
                "name": "Currency validity",
                "required": True,
                "check_type": "allowed_value",
                "input_columns": ["currency"],
                "allowed_values": ["USD", "EUR", "GBP"],
            }
        ],
    )
    sanitized = _sanitize_validation_checks_payload(payload)
    assert sanitized["allowed_enums"]["currency"] == ["USD", "EUR", "GBP"]
    assert sanitized["validation_checks"] == []


def test_sanitize_validation_checks_moves_categories_to_top_level_allowed_enums_and_derives_metadata() -> None:
    payload = _minimal_contract_payload(
        input_columns=["invoice_id", "due_date"],
        output_columns=["invoice_id", "aging_bucket", "issue_flag"],
        validation_checks=[
            {
                "name": "Aging bucket validity",
                "required": True,
                "categories": ["0-30 days", "31-60 days", "61+ days"],
            }
        ],
    )
    sanitized = _sanitize_validation_checks_payload(payload)
    assert sanitized["allowed_enums"]["aging_bucket"] == [
        "0-30 days",
        "31-60 days",
        "61+ days",
    ]
    assert sanitized["validation_checks"] == []


def test_sanitize_validation_checks_derives_check_id_and_layer_when_safe() -> None:
    payload = _minimal_contract_payload(
        validation_checks=[
            {
                "name": "Amount positive",
                "required": True,
                "check_type": "formula",
                "input_columns": ["amount"],
                "output_column": "amount",
                "formula": "amount > 0",
            }
        ],
    )
    sanitized = _sanitize_validation_checks_payload(payload)
    check = sanitized["validation_checks"][0]
    assert check["check_id"] == "amount_positive"
    assert check["layer"] == "business_rules"


def test_nested_validation_checks_do_not_fail_contract_planning_when_sanitized(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = _nested_validation_check_contract_payload()
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [
            _response(
                "nested-validation-check-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            *script[1:],
        ],
    )
    assert result.ok is True
    assert "contract_schema_repair" not in _purposes(event_log, sid)
    assert "code_generation" in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["allowed_enums"]["category"]
    assert all("allowed_enums" not in check for check in saved["validation_checks"])


def test_contract_validation_issue_details_for_nested_validation_checks() -> None:
    payload = _nested_validation_check_contract_payload()
    with pytest.raises(ValidationError) as exc_info:
        AuthorOutputContract.model_validate(payload)
    issues = _contract_validation_issue_details(exc_info.value)
    by_path = {issue["path"]: issue for issue in issues}
    assert by_path["validation_checks[1].allowed_enums"]["forbidden_field"] == "allowed_enums"
    assert by_path["validation_checks[2].min_value"]["forbidden_field"] == "min_value"
    assert by_path["validation_checks[2].max_value"]["forbidden_field"] == "max_value"


def test_planning_prompt_includes_validation_check_guidance(workspaces_root: Path) -> None:
    script = _base_script()
    model = FakeModelClient(script=script)
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_planning_validation_check_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    guidance = prompt["validation_check_guidance"]
    assert guidance["allowed_fields"] == [
        "check_id",
        "layer",
        "name",
        "required",
        "check_type",
        "output_file",
        "formula",
        "input_columns",
        "output_column",
        "tolerance",
        "severity",
    ]
    assert "allowed_enums" in guidance["forbidden_nested_fields"]
    assert "Never put allowed_enums, allowed_values, categories, min_value, max_value" in "\n".join(
        guidance["planning_rules"]
    )
    assert guidance["valid_examples"]["valid_formula_range_check"]["formula"] == (
        "0 <= confidence <= 1"
    )


def test_planning_prompt_includes_output_column_semantics_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    model = FakeModelClient(script=script)
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_planning_output_semantics_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    guidance = prompt["output_column_semantics_guidance"]
    assert "copied_input" in guidance["producer_kind_examples"]
    assert "copied unchanged" in guidance["valid_examples"]["required_passthrough_input_column"][
        "row_semantics"
    ].lower()
    assert "Only place passthrough input columns in required_output_columns" in "\n".join(
        guidance["planning_rules"]
    )


def test_planning_prompt_includes_calculated_field_and_exception_rule_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    model = FakeModelClient(script=script)
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_planning_calc_exception_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert prompt["calculated_field_guidance"]["required_fields"] == ["name", "description"]
    assert prompt["exception_rule_guidance"]["forbidden_fields"] == ["issue_flag"]
    assert "Use calculated_field_guidance for calculated_fields" in prompt["instruction"]
    assert "exception_rule_guidance for exception_rules" in prompt["instruction"]


def test_output_column_semantics_guidance_includes_passthrough_rules() -> None:
    guidance = _output_column_semantics_guidance()
    rules = "\n".join(guidance["planning_rules"])
    assert guidance["allowed_fields"] == [
        "name",
        "description",
        "producer_kind",
        "required",
        "nullable",
        "allow_empty_string",
        "row_semantics",
        "fallback_value_semantics",
    ]
    assert "copied_input" in guidance["producer_kind_examples"]
    assert "copied unchanged" in guidance["valid_examples"]["required_passthrough_input_column"][
        "row_semantics"
    ].lower()
    assert "do not invent a new derived business value" in guidance["valid_examples"][
        "required_passthrough_input_column"
    ]["fallback_value_semantics"].lower()
    assert "Only place passthrough input columns in required_output_columns" in rules


def test_required_output_semantics_guidance_classifies_passthrough_and_derived() -> None:
    guidance = _required_output_semantics_guidance(
        payload={
            "input_columns": ["invoice_id", "amount", "due_date"],
            "required_output_columns": ["invoice_id", "aging_bucket", "residual_balance"],
            "output_column_semantics": [],
            "calculated_fields": [
                {
                    "name": "residual_balance",
                    "description": "Residual remaining after applications",
                    "formula": "amount",
                }
            ],
        },
        schema_profile={"columns": ["invoice_id", "amount", "due_date"]},
    )
    assert guidance["missing_passthrough_required_columns"] == ["invoice_id"]
    assert guidance["missing_derived_required_columns"] == [
        "aging_bucket",
        "residual_balance",
    ]
    assert guidance["passthrough_semantics_template"]["producer_kind"] == "copied_input"
    hints = {entry["name"]: entry for entry in guidance["derived_column_hints"]}
    assert hints["aging_bucket"]["classification"] == "derived_required_output"
    assert hints["residual_balance"]["classification"] == "derived_calculation"
    assert hints["residual_balance"]["calculated_field"]["formula"] == "amount"


def test_schema_repair_prompt_includes_validation_check_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "condition": "confidence < 0.8",
        }
    ]
    corrected_contract = dict(invalid_contract)
    corrected_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "source_column": "confidence",
            "filter": "confidence < 0.8",
        }
    ]
    model = FakeModelClient(
        script=[
            _response(
                "summary-metric-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "summary-metric-corrected-repair",
                {"author_output_contract": corrected_contract, "planning_notes": []},
            ),
            *script[2:],
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_schema_repair_validation_check_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    repair_prompt = json.loads(model.calls[1]["messages"][0]["content"][0]["text"])
    guidance = repair_prompt["validation_check_guidance"]
    assert guidance["repair_rule"]
    assert "validation_checks[*].allowed_enums" in guidance["repair_rule"]
    assert "min_value" in guidance["repair_rule"]
    assert "Never return the same forbidden nested keys" in guidance["repair_rule"]
    assert "validation_check_guidance.repair_rule" in repair_prompt["instruction"]


def test_planning_prompt_includes_artifact_path_namespace_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    model = FakeModelClient(script=script)
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_planning_artifact_path_namespace_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    guidance = prompt["artifact_path_namespace_guidance"]
    assert guidance["allowed_output_prefixes"] == ["outputs/", "reports/"]
    assert "summaries/" in guidance["forbidden_output_prefixes"]
    assert "artifact_path_namespace_guidance" in prompt["instruction"]


def test_planning_prompt_includes_allowed_enums_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    model = FakeModelClient(script=script)
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_planning_allowed_enums_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    guidance = prompt["allowed_enums_guidance"]
    assert guidance["valid_shape"]["allowed_enums"]["status"] == ["open", "closed"]
    assert "plain JSON arrays" in "\n".join(guidance["planning_rules"])
    assert "allowed_enums_guidance" in prompt["instruction"]


def test_review_prompt_includes_contract_return_shape_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    model = FakeModelClient(script=script)
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_review_return_shape_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    review_prompt = next(
        json.loads(call["messages"][0]["content"][0]["text"])
        for call in model.calls
        if json.loads(call["messages"][0]["content"][0]["text"]).get("stage") == "contract_review"
    )
    guidance = review_prompt["contract_return_shape_guidance"]
    assert "input_schema_path" in guidance["forbidden_top_level_fields"]
    assert "contract_return_shape_guidance" in review_prompt["instruction"]


def test_schema_repair_prompt_includes_contract_return_shape_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "condition": "confidence < 0.8",
        }
    ]
    corrected_contract = dict(invalid_contract)
    corrected_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "source_column": "confidence",
            "filter": "confidence < 0.8",
        }
    ]
    model = FakeModelClient(
        script=[
            _response(
                "meta-leak-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "meta-leak-corrected-repair",
                {"author_output_contract": corrected_contract, "planning_notes": []},
            ),
            *script[2:],
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_schema_repair_return_shape_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    repair_prompt = json.loads(model.calls[1]["messages"][0]["content"][0]["text"])
    assert repair_prompt["stage"] == "contract_schema_repair"
    guidance = repair_prompt["contract_return_shape_guidance"]
    assert "calculated_field_guidance" in guidance["forbidden_top_level_fields"]
    assert "contract_return_shape_guidance" in repair_prompt["instruction"]


def test_schema_repair_prompt_includes_artifact_path_namespace_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["summary_output_files"] = ["validation/evidence.json"]
    corrected_contract = dict(invalid_contract)
    corrected_contract["summary_output_files"] = ["outputs/evidence.json"]
    model = FakeModelClient(
        script=[
            _response(
                "summary-path-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "summary-path-corrected-repair",
                {"author_output_contract": corrected_contract, "planning_notes": []},
            ),
            *script[2:],
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_schema_repair_artifact_path_namespace_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    repair_prompt = json.loads(model.calls[1]["messages"][0]["content"][0]["text"])
    assert repair_prompt["stage"] == "contract_schema_repair"
    guidance = repair_prompt["artifact_path_namespace_guidance"]
    assert "summaries/<filename> -> outputs/<filename>" in guidance["mapping_rules"]
    assert "artifact_path_namespace_guidance.repair_rule" in repair_prompt["instruction"]


def test_schema_repair_prompt_includes_allowed_enums_guidance(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_contract = dict(_payload(script[0])["author_output_contract"])
    invalid_contract["allowed_enums"] = {
        "status": {"labels": ["open", "closed"]},
    }
    corrected_contract = dict(invalid_contract)
    corrected_contract["allowed_enums"] = {"status": ["open", "closed"]}
    model = FakeModelClient(
        script=[
            _response(
                "allowed-enums-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "allowed-enums-corrected-repair",
                {"author_output_contract": corrected_contract, "planning_notes": []},
            ),
            *script[2:],
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_schema_repair_allowed_enums_guidance",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    repair_prompt = json.loads(model.calls[1]["messages"][0]["content"][0]["text"])
    assert repair_prompt["stage"] == "contract_schema_repair"
    guidance = repair_prompt["allowed_enums_guidance"]
    assert "plain JSON arrays" in "\n".join(guidance["planning_rules"])
    assert "allowed_enums_guidance.repair_rule" in repair_prompt["instruction"]


def test_schema_repair_prompt_includes_schema_dialect_guidance(
    workspaces_root: Path,
) -> None:
    invalid_contract = _minimal_contract_payload(
        calculated_fields=[{"name": "residual_balance", "formula": "amount"}],
        exception_rules=[
            {
                "name": "requires_review",
                "condition": "amount > 0",
                "issue_flag": "Review required",
            }
        ],
        validation_checks=[
            {
                "name": "Currency validity",
                "required": True,
                "check_type": "allowed_value",
                "input_columns": ["amount"],
                "allowed_values": ["GBP"],
            }
        ],
        summary_metrics=[
            {
                "name": "uncertain_rows_count",
                "metric_type": "count",
                "condition": "amount > 0",
            }
        ],
    )
    invalid_contract.pop("input_file", None)
    invalid_contract["preserve_original_data"] = True
    repaired_contract = _minimal_contract_payload()
    model = FakeModelClient(
        script=[
            _response(
                "repair-schema-dialect-guidance",
                {"author_output_contract": repaired_contract},
            )
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    responses_dir = workspace / "generated" / "model_responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        _validate_contract_or_repair(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_schema_dialect_guidance",
            model_client=model,
            responses_dir=responses_dir,
            source_stage="contract_planning",
            payload=invalid_contract,
            schema_profile={
                "columns": ["invoice_id", "amount"],
                "row_count": 3,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            user_description="Review finance rows.",
        )
    )
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert prompt["calculated_field_guidance"]["required_fields"] == ["name", "description"]
    assert prompt["exception_rule_guidance"]["forbidden_fields"] == ["issue_flag"]
    instruction = prompt["instruction"]
    assert "validation_checks[*].allowed_values" in instruction
    assert "categories" in instruction
    assert "calculated_fields[*].description missing" in instruction
    assert "exception_rules[*].issue_flag" in instruction
    assert "preserve_original_data, enable_logging, or enable_debugging" in instruction


def test_schema_repair_prompt_includes_required_output_semantics_guidance(
    workspaces_root: Path,
) -> None:
    invalid_contract = _minimal_contract_payload(
        input_columns=["invoice_id", "amount", "due_date"],
        output_columns=["invoice_id", "amount", "due_date", "aging_bucket"],
        required_output_columns=["invoice_id", "amount", "aging_bucket"],
        output_column_semantics=[],
        calculated_fields=[],
    )
    repaired_contract = _minimal_contract_payload(
        input_columns=["invoice_id", "amount", "due_date"],
        output_columns=["invoice_id", "amount", "due_date", "aging_bucket"],
        required_output_columns=["invoice_id", "amount", "aging_bucket"],
        output_column_semantics=[
            {
                "name": "invoice_id",
                "description": "Copied invoice identifier for traceability",
                "producer_kind": "copied_input",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Copied unchanged from the source row into the clean row-level output to preserve traceability.",
                "fallback_value_semantics": "Carry the normalized source-row value through unchanged and do not invent a new derived business value for this copied input field.",
            },
            {
                "name": "amount",
                "description": "Copied invoice amount for traceability",
                "producer_kind": "copied_input",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Copied unchanged from the source row into the clean row-level output to preserve traceability.",
                "fallback_value_semantics": "Carry the normalized source-row value through unchanged and do not invent a new derived business value for this copied input field.",
            },
            {
                "name": "aging_bucket",
                "description": "Assigned aging bucket for finance review",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Assign an aging bucket to each row using the workflow's aging policy and due-date context.",
                "fallback_value_semantics": "Use an explicit fallback review bucket when the aging assignment cannot be determined from the row inputs.",
            },
        ],
    )
    model = FakeModelClient(
        script=[
            _response(
                "repair-required-output-semantics",
                {"author_output_contract": repaired_contract},
            )
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    responses_dir = workspace / "generated" / "model_responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    contract, error = asyncio.run(
        _validate_contract_or_repair(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_required_output_semantics_repair",
            model_client=model,
            responses_dir=responses_dir,
            source_stage="contract_planning",
            payload=invalid_contract,
            schema_profile={
                "columns": ["invoice_id", "amount", "due_date"],
                "row_count": 3,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            user_description=(
                "Prepare a finance review file, preserve source identifiers, and assign "
                "aging buckets as of 2026-05-01."
            ),
        )
    )
    assert error is None
    assert contract is not None
    semantics = contract.output_column_semantics_by_name()
    assert semantics["invoice_id"].producer_kind == "copied_input"
    assert semantics["amount"].producer_kind == "copied_input"
    assert semantics["aging_bucket"].producer_kind != "copied_input"
    repair_prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    guidance = repair_prompt["required_output_semantics_guidance"]
    # Passthrough semantics sanitizer runs before schema repair, so exact input
    # column matches are auto-repaired and no longer appear as missing.
    assert guidance["missing_passthrough_required_columns"] == []
    assert guidance["missing_derived_required_columns"] == ["aging_bucket"]
    assert repair_prompt["user_description"].startswith("Prepare a finance review file")
    assert "required_output_semantics_guidance.repair_rule" in repair_prompt["instruction"]


def test_schema_repair_prompt_includes_deliverable_shape_and_input_file_guidance(
    workspaces_root: Path,
) -> None:
    invalid_contract = _minimal_contract_payload(
        requested_deliverables=[{"path": "outputs/output.csv", "required": True}],
        summary_metrics=[
            {
                "name": "uncertain_rows_count",
                "metric_type": "count",
                "condition": "amount > 0",
            }
        ],
    )
    invalid_contract.pop("input_file", None)
    invalid_contract["output_format"] = "csv"
    repaired_contract = _minimal_contract_payload(
        requested_deliverables=[
            {
                "name": "output",
                "output_path": "outputs/output.csv",
                "required": True,
            }
        ],
        summary_metrics=[
            {
                "name": "uncertain_rows_count",
                "metric_type": "count",
                "source_column": "amount",
                "filter": "amount > 0",
            }
        ],
    )
    model = FakeModelClient(
        script=[
            _response(
                "repair-deliverable-shape-and-input-file",
                {"author_output_contract": repaired_contract},
            )
        ]
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    responses_dir = workspace / "generated" / "model_responses"
    responses_dir.mkdir(parents=True, exist_ok=True)

    contract, error = asyncio.run(
        _validate_contract_or_repair(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_deliverable_shape_repair_prompt",
            model_client=model,
            responses_dir=responses_dir,
            source_stage="contract_review",
            payload=invalid_contract,
            schema_profile={
                "columns": ["invoice_id", "amount"],
                "row_count": 3,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "normalized_input_path": "uploads/sample_input.csv",
            },
            user_description="Review finance rows and produce a validation report.",
        )
    )

    assert error is None
    assert contract is not None
    repair_prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    rules = "\n".join(repair_prompt["rules"])
    assert "Structured DeliverableSpec objects must use output_path, not path." in rules
    assert "Do not emit output_format" in rules
    instruction = repair_prompt["instruction"]
    assert "required_file_profile_fields.input_file exactly" in instruction
    assert "remove output_format entirely" in instruction
    assert "rename that field to output_path" in instruction
    assert "smallest correction needed" in instruction


def test_reviewed_contract_shape_is_sanitized_without_review_time_schema_repair(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    invalid_reviewed_contract = dict(_payload(script[1])["reviewed_contract"])
    invalid_reviewed_contract.pop("input_file", None)
    invalid_reviewed_contract["output_format"] = "csv"
    invalid_reviewed_contract["requested_deliverables"] = [
        {"path": "outputs/output.csv", "required": True},
        {"path": "reports/validation_report.md", "required": True},
    ]
    invalid_review = _response(
        "reviewed-contract-invalid-shape-but-sanitizable",
        {"reviewed_contract": invalid_reviewed_contract, "approved": True},
    )

    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [script[0], invalid_review, script[2], script[3]],
    )

    assert result.ok is True
    assert "contract_schema_repair" not in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "author_output_contract.json").read_text(encoding="utf-8")
    )
    assert saved["input_file"] == "uploads/sample_input.csv"
    assert "output_format" not in saved
    assert saved["requested_deliverables"][0]["name"] == "output"
    assert saved["requested_deliverables"][0]["output_path"] == "outputs/output.csv"
    assert saved["requested_deliverables"][0]["required"] is True
    assert saved["requested_deliverables"][1]["name"] == "validation_report"
    assert saved["requested_deliverables"][1]["output_path"] == "reports/validation_report.md"
    assert saved["requested_deliverables"][1]["required"] is True


def test_invalid_nested_validation_checks_fail_closed_when_repair_returns_same_forbidden_fields(
    workspaces_root: Path,
) -> None:
    invalid_contract = _nested_validation_check_contract_payload()
    invalid_contract["summary_metrics"] = [
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "condition": "confidence < 0.8",
        }
    ]
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [
            _response(
                "nested-check-and-metric-invalid-plan",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
            _response(
                "nested-check-and-metric-invalid-repair",
                {"author_output_contract": invalid_contract, "planning_notes": []},
            ),
        ],
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    assert "contract_schema_repair" in _purposes(event_log, sid)
    assert "code_generation" not in _purposes(event_log, sid)
    assert not (workspace / "generated" / "agent.py").exists()


def test_contract_review_failure_user_message_hides_pydantic_noise() -> None:
    result = _contract_review_failed_result(
        technical_detail=(
            "1 validation error for AuthorOutputContract\nsummary_metrics.0\n"
            "  Input should be a valid string"
        ),
        stages=["contract_planning", "contract_review"],
    )
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED
    assert "validation error for AuthorOutputContract" not in (result.message or "")
    assert "validation error for AuthorOutputContract" in (result.technical_detail or "")
