"""Scripted model responses for LLM-first Author orchestration tests.

These fixtures simulate model output. They intentionally do not read or copy
checked-in Author templates.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentforge.models import ModelResponse, TextBlock


def _contract_plan_payload(*, workflow_type: str, contract_overrides: dict | None = None) -> dict:
    if workflow_type == "payment_processor_reconciliation":
        input_columns = [
            "transaction_id",
            "processor",
            "settlement_batch",
            "gross_amount",
            "fee_amount",
            "net_amount",
            "status",
        ]
        required = [
            "calculated_net_amount",
            "expected_net_amount",
            "net_amount_difference",
            "reconciliation_status",
            "issue_flag",
            "rule_used",
            "confidence",
        ]
        payload: dict = {
            "workflow_type": workflow_type,
            "workflow_confidence": 0.93,
            "build_mode": "llm_custom",
            "input_file": "uploads/normalised_input.csv",
            "input_format": "xlsx",
            "selected_sheet": "Sheet1",
            "normalized_input_path": "uploads/normalised_input.csv",
            "primary_sheet": "Sheet1",
            "reference_sheets": [],
            "as_of_date": None,
            "requires_model_planning": True,
            "primary_row_key": "transaction_id",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [],
            "exception_output_files": [],
            "input_columns": input_columns,
            "output_columns": input_columns + required,
            "required_output_columns": required,
            "optional_output_columns": [],
            "output_column_semantics": [
                {
                    "name": "calculated_net_amount",
                    "description": "Computed gross amount minus processor fee",
                    "producer_kind": "calculation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit the calculated net amount for every output row.",
                    "fallback_value_semantics": "If numeric parsing fails, still emit an explicit calculated value string such as 0.00.",
                },
                {
                    "name": "expected_net_amount",
                    "description": "Reported net amount carried into the output row",
                    "producer_kind": "calculation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty expected net amount value for every output row.",
                    "fallback_value_semantics": "If the source value is unavailable, emit an explicit fallback numeric string rather than a blank.",
                },
                {
                    "name": "net_amount_difference",
                    "description": "Difference between calculated and reported net amounts",
                    "producer_kind": "calculation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit the per-row difference between calculated and reported net amount.",
                    "fallback_value_semantics": "If calculation inputs are unavailable, emit an explicit numeric fallback string rather than a blank.",
                },
                {
                    "name": "reconciliation_status",
                    "description": "Matched or review status for the row",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty reconciliation decision for every output row.",
                    "fallback_value_semantics": "If no specific match rule applies, emit an explicit review-style status.",
                },
                {
                    "name": "issue_flag",
                    "description": "Row-level exception flag",
                    "producer_kind": "exception_flag",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty exception flag for every output row.",
                    "fallback_value_semantics": "Use an explicit default non-exception flag when no exception rule matches.",
                },
                {
                    "name": "rule_used",
                    "description": "Audit label describing which reconciliation rule path was used",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule label or explanation describing the exact reconciliation rule path used for this row.",
                    "fallback_value_semantics": "Use an explicit default rule label when the row takes the fallback path.",
                },
                {
                    "name": "confidence",
                    "description": "Confidence score for the row-level decision",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty confidence value for every output row.",
                    "fallback_value_semantics": "If confidence is uncertain, emit an explicit low-confidence value rather than a blank.",
                },
            ],
            "calculated_fields": [
                {
                    "name": "calculated_net_amount",
                    "description": "gross amount minus processor fee",
                    "formula": "gross_amount - fee_amount",
                },
                {
                    "name": "net_amount_difference",
                    "description": "calculated net amount minus reported net amount",
                    "formula": "calculated_net_amount - net_amount",
                },
            ],
            "formula_input_columns": ["gross_amount", "fee_amount", "net_amount"],
            "formula_output_columns": ["calculated_net_amount", "net_amount_difference"],
            "tolerances": {"calculated_net_amount": 0.01, "net_amount_difference": 0.01},
            "aggregation_specs": [],
            "summary_group_keys": [],
            "summary_metrics": [],
            "exception_rules": [
                "Flag rows where reported net_amount differs from gross_amount minus fee_amount by more than 0.01.",
                "Flag rows whose status is not settled.",
            ],
            "classification_rules": [
                {
                    "name": "payment_reconciliation_status",
                    "description": "Matched when net amount agrees and status is settled.",
                }
            ],
            "validation_checks": [
                {"check_id": "required_columns", "layer": "required_columns", "name": "Required columns"},
                {"check_id": "row_count", "layer": "row_level", "name": "Row count preserved"},
                {"check_id": "formulas", "layer": "business_rules", "name": "Calculated fields"},
                {"check_id": "generated_pytest", "layer": "generated_pytest", "name": "Generated pytest suite"},
            ],
            "golden_comparison_requirement": "skipped",
            "golden_output_path": None,
            "skipped_checks": ["golden_output"],
            "warnings": [],
            "clarification_questions": [],
            "unsupported_reasons": [],
            "requested_deliverables": ["outputs/output.csv"],
            "produced_deliverables": [],
            "missing_deliverables": [],
            "preserve_row_count": True,
            "allowed_enums": {
                "issue_flag": ["yes", "no"],
                "reconciliation_status": ["matched", "review"],
            },
        }
    else:
        input_columns = ["txn_id", "date", "amount", "description", "counterparty", "account"]
        required = ["category", "rule_matched", "rule_used", "confidence"]
        payload = {
            "workflow_type": workflow_type,
            "workflow_confidence": 0.9,
            "build_mode": "llm_custom",
            "input_file": "uploads/sample_input.csv",
            "input_format": "csv",
            "selected_sheet": None,
            "normalized_input_path": "uploads/sample_input.csv",
            "primary_sheet": None,
            "reference_sheets": [],
            "as_of_date": None,
            "requires_model_planning": True,
            "primary_row_key": "txn_id",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [],
            "exception_output_files": [],
            "input_columns": input_columns,
            "output_columns": input_columns + required,
            "required_output_columns": required,
            "optional_output_columns": [],
            "output_column_semantics": [
                {
                    "name": "category",
                    "description": "Final category assigned to the transaction row",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit exactly one non-empty category label for every output row.",
                    "fallback_value_semantics": "If no specific classification rule matches, emit an explicit default uncategorised label.",
                },
                {
                    "name": "rule_matched",
                    "description": "Evidence string describing which rule matched for the row",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule match explanation for every output row.",
                    "fallback_value_semantics": "If no specific rule matches, emit an explicit no-match explanation string.",
                },
                {
                    "name": "rule_used",
                    "description": "Audit label describing the rule path used for the row",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule identifier or rule description for every output row.",
                    "fallback_value_semantics": "If no specific rule matches, emit an explicit default rule label rather than a blank.",
                },
                {
                    "name": "confidence",
                    "description": "Confidence score for the assigned category",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty confidence value for every output row.",
                    "fallback_value_semantics": "If classification is uncertain, emit an explicit low-confidence value rather than a blank.",
                },
            ],
            "calculated_fields": [],
            "formula_input_columns": [],
            "formula_output_columns": [],
            "tolerances": {},
            "aggregation_specs": [],
            "summary_group_keys": [],
            "summary_metrics": [],
            "exception_rules": [],
            "classification_rules": [
                {
                    "name": "bank_category_keywords",
                    "description": "Assign categories from transaction text and amount sign.",
                }
            ],
            "allowed_enums": {
                "category": [
                    "Income",
                    "Office Expense",
                    "Travel",
                    "Subscriptions",
                    "Refund",
                    "Uncategorised",
                ]
            },
            "validation_checks": [
                {"check_id": "required_columns", "layer": "required_columns", "name": "Required columns"},
                {"check_id": "row_count", "layer": "row_level", "name": "Row count preserved"},
                {"check_id": "allowed_enums", "layer": "business_rules", "name": "Allowed categories"},
                {"check_id": "generated_pytest", "layer": "generated_pytest", "name": "Generated pytest suite"},
            ],
            "golden_comparison_requirement": "skipped",
            "golden_output_path": None,
            "skipped_checks": ["golden_output"],
            "warnings": [],
            "clarification_questions": [],
            "unsupported_reasons": [],
            "requested_deliverables": ["outputs/output.csv"],
            "produced_deliverables": [],
            "missing_deliverables": [],
            "preserve_row_count": True,
        }
    if contract_overrides:
        payload.update(contract_overrides)
        summary_files = payload.get("summary_output_files") or []
        if "outputs/summary_by_settlement_batch.csv" in summary_files:
            payload["aggregation_specs"] = [
                {
                    "group_key": "settlement_batch",
                    "output_path": "outputs/summary_by_settlement_batch.csv",
                    "metrics": [
                        "transaction_count",
                        "gross_total",
                        "fee_total",
                        "net_total",
                        "calculated_net_total",
                        "issue_count",
                    ],
                }
            ]
            payload["summary_group_keys"] = ["settlement_batch"]
            payload["summary_metrics"] = [
                "transaction_count",
                "gross_total",
                "fee_total",
                "net_total",
                "calculated_net_total",
                "issue_count",
            ]
            if "outputs/summary_by_settlement_batch.csv" not in payload["requested_deliverables"]:
                payload["requested_deliverables"].append("outputs/summary_by_settlement_batch.csv")
    return payload


def _agent_source() -> str:
    return r'''
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _decimal(value):
    text = str(value or "").strip()
    if not text:
        return Decimal("0")
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows, fieldnames):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _category_for(row):
    text = " ".join([row.get("description", ""), row.get("counterparty", "")]).lower()
    amount = _decimal(row.get("amount"))
    if amount > 0:
        return "Income", "positive amount"
    if "uber" in text or "lyft" in text or "airline" in text or "hotel" in text:
        return "Travel", "travel keyword"
    if "aws" in text or "saas" in text or "subscription" in text:
        return "Subscriptions", "subscription keyword"
    if "refund" in text:
        return "Refund", "refund keyword"
    if "office" in text or "staples" in text:
        return "Office Expense", "office keyword"
    return "Uncategorised", "no rule matched"


def _payment_row(row):
    gross = _decimal(row.get("gross_amount"))
    fee = _decimal(row.get("fee_amount"))
    reported = _decimal(row.get("net_amount"))
    calculated = gross - fee
    difference = calculated - reported
    status = str(row.get("status") or "").strip().lower()
    issue = abs(difference) > Decimal("0.01") or status != "settled"
    result = dict(row)
    result["calculated_net_amount"] = f"{calculated:.2f}"
    result["expected_net_amount"] = f"{reported:.2f}"
    result["net_amount_difference"] = f"{difference:.2f}"
    result["reconciliation_status"] = "review" if issue else "matched"
    result["issue_flag"] = "yes" if issue else "no"
    if abs(difference) > Decimal("0.01"):
        result["rule_used"] = "net_amount_mismatch"
    elif status != "settled":
        result["rule_used"] = "status_not_settled"
    else:
        result["rule_used"] = "matched"
    result["confidence"] = "0.95"
    return result


def _bank_row(row):
    category, rule = _category_for(row)
    result = dict(row)
    result["category"] = category
    result["rule_matched"] = rule
    result["rule_used"] = rule
    result["confidence"] = "0.90" if category != "Uncategorised" else "0.55"
    return result


def _normalise_rows(rows, contract):
    workflow = str(contract.get("workflow_type") or "")
    if "payment" in workflow or "reconciliation" in workflow:
        return [_payment_row(row) for row in rows]
    return [_bank_row(row) for row in rows]


def _write_summaries(rows, contract):
    for spec in contract.get("aggregation_specs") or []:
        group_key = spec.get("group_key")
        output_path = spec.get("output_path")
        if not group_key or not output_path:
            continue
        grouped = defaultdict(list)
        for row in rows:
            grouped[row.get(group_key, "")].append(row)
        summary_rows = []
        for key, group_rows in sorted(grouped.items()):
            summary_rows.append({
                group_key: key,
                "transaction_count": len(group_rows),
                "gross_total": f"{sum(_decimal(r.get('gross_amount')) for r in group_rows):.2f}",
                "fee_total": f"{sum(_decimal(r.get('fee_amount')) for r in group_rows):.2f}",
                "net_total": f"{sum(_decimal(r.get('net_amount')) for r in group_rows):.2f}",
                "calculated_net_total": f"{sum(_decimal(r.get('calculated_net_amount')) for r in group_rows):.2f}",
                "issue_count": sum(1 for r in group_rows if str(r.get("issue_flag", "")).lower() == "yes"),
            })
        fieldnames = [group_key, "transaction_count", "gross_total", "fee_total", "net_total", "calculated_net_total", "issue_count"]
        _write_csv(output_path, summary_rows, fieldnames)


def _write_exceptions(rows, contract, fieldnames):
    for output_path in contract.get("exception_output_files") or []:
        flagged = [row for row in rows if str(row.get("issue_flag") or row.get("exception_flag") or "").lower() in {"yes", "true", "1"}]
        _write_csv(output_path, flagged, fieldnames)


def _write_report(path, rows, contract):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Validation Report\\n\\n"
        f"- Workflow: {contract.get('workflow_type', 'unknown')}\\n"
        f"- Rows processed: {len(rows)}\\n",
        encoding="utf-8",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generated Author agent")
    parser.add_argument("--input", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--row-output", required=True)
    parser.add_argument("--report-path", required=True)
    args = parser.parse_args(argv)

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    rows = _read_csv(args.input)
    output_rows = _normalise_rows(rows, contract)
    output_columns = contract.get("output_columns") or list(output_rows[0].keys() if output_rows else [])
    for row in output_rows:
        for column in output_columns:
            row.setdefault(column, "")
    _write_csv(args.row_output, output_rows, output_columns)
    _write_summaries(output_rows, contract)
    _write_exceptions(output_rows, contract, output_columns)
    _write_report(args.report_path, output_rows, contract)


if __name__ == "__main__":
    raise SystemExit(main())
'''.lstrip()


def _test_source() -> str:
    return r'''
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path


def _load_agent():
    spec = importlib.util.spec_from_file_location("generated_agent", Path("generated/agent.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _run_agent(contract):
    contract_path = Path("generated/author_output_contract.json")
    input_path = contract.get("normalized_input_path") or contract["input_file"]
    agent = _load_agent()
    report_path = "reports/validation_report.md"
    for entry in contract.get("requested_deliverables", []):
        if isinstance(entry, dict) and entry.get("required") and str(entry.get("output_path", "")).endswith(".md"):
            report_path = entry["output_path"]
            break
    for output_path in contract.get("summary_output_files", []):
        candidate = output_path["path"] if isinstance(output_path, dict) else output_path
        if str(candidate).lower().endswith(".md"):
            report_path = candidate
            break
    agent.main([
        "--input",
        input_path,
        "--contract",
        str(contract_path),
        "--row-output",
        contract["row_level_output_file"],
        "--report-path",
        report_path,
    ])
    return input_path, report_path


def test_generated_agent_row_count_preserved():
    # Trace: row count preservation when requested
    contract = json.loads(Path("generated/author_output_contract.json").read_text(encoding="utf-8"))
    input_path, _report_path = _run_agent(contract)
    output_rows = _read_csv(contract["row_level_output_file"])
    input_rows = _read_csv(input_path)
    assert len(output_rows) == len(input_rows)


def test_generated_agent_required_columns_non_null():
    # Trace: required non-null output columns
    contract = json.loads(Path("generated/author_output_contract.json").read_text(encoding="utf-8"))
    _run_agent(contract)
    output_rows = _read_csv(contract["row_level_output_file"])
    for column in contract["required_output_columns"]:
        assert column in output_rows[0]
    for row in output_rows:
        for column in contract["required_output_columns"]:
            assert str(row.get(column, "")).strip()


def test_generated_agent_required_artifacts_exist():
    # Trace: required artifacts from reviewed contract
    contract = json.loads(Path("generated/author_output_contract.json").read_text(encoding="utf-8"))
    _input_path, report_path = _run_agent(contract)
    assert Path(contract["row_level_output_file"]).is_file()
    assert Path(report_path).is_file()
    if "calculated_net_amount" in contract["required_output_columns"]:
        first = _read_csv(contract["row_level_output_file"])[0]
        expected = float(first["gross_amount"]) - float(first["fee_amount"])
        assert abs(float(first["calculated_net_amount"]) - expected) < 0.01
    if "category" in contract["required_output_columns"]:
        assert _read_csv(contract["row_level_output_file"])[0]["category"]
'''.lstrip()


def _files_list(files: dict[str, str]) -> list[dict[str, str]]:
    return [{"path": path, "content": content} for path, content in sorted(files.items())]


def model_authoring_responses(
    *,
    template_root: Path,
    workflow_type: str,
    contract_overrides: dict | None = None,
) -> list[ModelResponse]:
    """Build a four-stage FakeModelClient script with model-emitted artifacts."""
    del template_root
    contract_plan = _contract_plan_payload(
        workflow_type=workflow_type,
        contract_overrides=contract_overrides,
    )
    agent_source = _agent_source()
    test_source = _test_source()
    code_plan = {
        "files_to_create": ["generated/agent.py"],
        "reasoning_summary": f"Author {workflow_type} agent from uploaded schema and contract.",
        "dependencies": [],
        "input_paths": [contract_plan["input_file"]],
        "output_paths": contract_plan.get("requested_deliverables", []),
        "tests_to_generate": [],
    }
    return [
        ModelResponse(
            id="author-contract-plan",
            content=[
                TextBlock(
                    text=json.dumps(
                        {"author_output_contract": contract_plan, "planning_notes": []},
                        indent=2,
                    )
                )
            ],
            stop_reason="end_turn",
        ),
        ModelResponse(
            id="author-contract-review",
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "approved": True,
                            "reviewed_contract": contract_plan,
                            "missing_deliverables": [],
                            "unsupported_assumptions": [],
                            "unclear_mappings": [],
                            "unsafe_logic": [],
                            "required_revisions": [],
                        },
                        indent=2,
                    )
                )
            ],
            stop_reason="end_turn",
        ),
        ModelResponse(
            id="author-code-generation",
            content=[TextBlock(text=agent_source)],
            stop_reason="end_turn",
        ),
        ModelResponse(
            id="author-test-generation",
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "files": [
                                {
                                    "path": "generated/tests/test_agent.py",
                                    "content": test_source,
                                }
                            ],
                            "notes": "Model-authored validation checks.",
                            "assumptions": [],
                        },
                        indent=2,
                    )
                )
            ],
            stop_reason="end_turn",
        ),
    ]


def model_ack_only_responses(count: int = 2) -> list[ModelResponse]:
    """Force model calls without material file contribution (guard tests)."""
    return [
        ModelResponse(
            id=f"author-ack-{idx}",
            content=[TextBlock(text="AUTHOR_MODEL_CONTRIBUTION_ACK")],
            stop_reason="end_turn",
        )
        for idx in range(count)
    ]
