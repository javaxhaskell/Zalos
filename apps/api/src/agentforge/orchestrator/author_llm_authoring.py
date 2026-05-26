"""LLM-first Author authoring — material model contribution required before completion."""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import json
import logging
import re
import shlex
import shutil
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from agentforge.config import Settings, deepseek_author_stage_models
from agentforge.models.client import ModelClient, ModelClientError, ModelMessage, TextBlock
from agentforge.models.deepseek_client import DeepSeekModelClient
from agentforge.models.ollama_client import OllamaModelClient
from agentforge.orchestrator.author_contract_validation import validate_supported_formula
from agentforge.orchestrator.tool_scope_audit import record_model_orchestrated_call
from agentforge.orchestrator.generated_agent_coercion import (
    codegen_data_coercion_guidance,
    codegen_data_coercion_section,
    data_coercion_repair_requirements,
    is_missing_numeric_coercion_typeerror,
)
from agentforge.persistence.event_log import EventLog
from agentforge.schemas import ActorType, ErrorCode, EventKind
from agentforge.schemas.author_output_contract import (
    AuthorOutputContract,
    ClarificationQuestion,
    DeliverableSpec,
    OutputColumnSemanticsSpec,
    OutputFileSpec,
    ValidationCheckSpec,
    deliverable_output_path,
    deliverable_required,
    deliverable_label,
    output_file_path,
)

AI_AUTHORED_WORKFLOW_BUILD_VIA = "ai_authored_workflow_build"
AI_ASSISTED_AUTHORING_VIA = AI_AUTHORED_WORKFLOW_BUILD_VIA
AI_ASSISTED_CUSTOM_BUILD_VIA = "ai_assisted_custom_build"
MODEL_PLANNED_WORKFLOW_BUILD_VIA = "model_planned_workflow_build"

AUTHOR_MODEL_REQUIRED_MESSAGE = (
    "Authoring requires model analysis of the uploaded file and workflow request, "
    "but no model call was made."
)
AUTHOR_MODEL_DID_NOT_CONTRIBUTE_MESSAGE = (
    "The model was called, but no model-authored or model-modified agent files were "
    "produced, so the Author workflow cannot claim completion."
)

_MODEL_STAGES = (
    "contract_planning",
    "contract_review",
    "code_generation",
    "code_adaptation",
    "test_generation",
    "validation_check_generation",
    "repair_iteration",
    "json_repair",
    "missing_files_repair",
    "safety_repair",
    "execution_repair",
)

_REQUIRED_CODE_STAGES = frozenset({"code_generation", "code_adaptation"})
_REQUIRED_TEST_STAGES = frozenset({"test_generation", "validation_check_generation"})

_REQUIRED_AGENT_PATH = "generated/agent.py"
_REQUIRED_TEST_SUFFIX = "tests/test_agent.py"

_UNSAFE_CODE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsocket\b", re.I), "network socket usage"),
    (re.compile(r"\burllib\b", re.I), "urllib network access"),
    (re.compile(r"\bhttpx\b", re.I), "httpx network access"),
    (re.compile(r"\brequests\b", re.I), "requests network access"),
    (re.compile(r"subprocess\.[^(]*shell\s*=\s*True", re.I), "shell subprocess"),
    (re.compile(r"\bos\.system\s*\(", re.I), "os.system()"),
    (re.compile(r"\bos\.environ\b", re.I), "environment secret access"),
    (re.compile(r"\beval\s*\(", re.I), "eval()"),
    (re.compile(r"\bexec\s*\(", re.I), "exec()"),
    (re.compile(r"\bcompile\s*\(", re.I), "compile()"),
    (re.compile(r"__import__\s*\(", re.I), "dynamic import"),
    (re.compile(r"importlib\.import_module\s*\(", re.I), "dynamic import"),
    (re.compile(r"open\s*\([^)]*['\"]\/etc", re.I), "reading system paths"),
)

_BACKEND_MANAGED_ARTIFACT_PATHS: frozenset[str] = frozenset(
    {
        "events.jsonl",
        "manifest.json",
        "SESSION_README.md",
        "archive.zip",
        "generated/author_output_contract.json",
        "reports/model_authoring_summary.md",
    }
)

_GENERATED_AGENT_FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "generated/model_responses/",
    "generated/debug/",
)

_WRITE_OPEN_MODES: frozenset[str] = frozenset({"w", "w+", "a", "a+", "x", "wb", "w+b", "ab", "a+b", "xb"})

_RESERVED_PATH_WRITE_REGEXES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(
            rf"open\s*\([^)]*['\"]{re.escape(path)}['\"][^)]*['\"](?:w|a|x)",
            re.I,
        ),
        path,
    )
    for path in sorted(_BACKEND_MANAGED_ARTIFACT_PATHS)
) + (
    (
        re.compile(
            rf"(?:Path\s*\(|['\"])({ '|'.join(re.escape(path) for path in sorted(_BACKEND_MANAGED_ARTIFACT_PATHS))})(?:['\"]\)|['\"])\.write_(?:text|bytes)\s*\(",
            re.I,
        ),
        "reserved path write",
    ),
)

_logger = logging.getLogger("agentforge.orchestrator.author_llm_authoring")


@dataclass
class ModelAuthoringResult:
    ok: bool
    error_code: ErrorCode | None = None
    message: str | None = None
    technical_detail: str | None = None
    output_contract: AuthorOutputContract | None = None
    contributed_files: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    repair_candidate_paths: list[str] = field(default_factory=list)
    repair_candidate_files: dict[str, str] = field(default_factory=dict)
    repair_candidate_promoted: bool = False
    repair_provider: str | None = None
    repair_model: str | None = None
    retryable: bool = False
    repair_failure_kind: str | None = None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_author_model_calls(events: list[Any]) -> int:
    return sum(1 for event in events if event.kind == EventKind.MODEL_CALLED)


def collect_author_model_stages(events: list[Any]) -> list[str]:
    stages: list[str] = []
    for event in events:
        if event.kind != EventKind.MODEL_CALLED:
            continue
        purpose = (event.payload or {}).get("purpose")
        if isinstance(purpose, str) and purpose in _MODEL_STAGES and purpose not in stages:
            stages.append(purpose)
    return stages


def reference_scaffold_model_stages_from_events(events: list[Any]) -> list[str]:
    """Model stages satisfied via bundled reference-sample contract scaffold."""
    stages: list[str] = []
    for event in events:
        if event.kind != EventKind.DECISION_INPUT:
            continue
        payload = event.payload or {}
        if payload.get("kind") != "reference_sample_contract_scaffold_used":
            continue
        stage = payload.get("stage")
        if isinstance(stage, str) and stage in _MODEL_STAGES and stage not in stages:
            stages.append(stage)
    return stages


def _reference_scaffold_mode_active(events: list[Any]) -> bool:
    """True only when the explicit dev scaffold flag was recorded for this session."""
    for event in events:
        if event.kind != EventKind.DECISION_INPUT:
            continue
        if (event.payload or {}).get("kind") == "reference_sample_scaffold_mode":
            return True
    return False


def effective_author_model_stages(events: list[Any]) -> list[str]:
    """Recorded model stages; scaffold equivalents count only when scaffold mode is explicit."""
    stages = collect_author_model_stages(events)
    if not _reference_scaffold_mode_active(events):
        return stages
    for stage in reference_scaffold_model_stages_from_events(events):
        if stage not in stages:
            stages.append(stage)
    return stages


def _latest_model_authoring_payload(events: list[Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for event in reversed(events):
        if event.kind != EventKind.DECISION_INPUT:
            continue
        event_payload = event.payload or {}
        if event_payload.get("kind") == "model_authoring_provenance":
            payload = event_payload
            break
    return payload


def model_contributed_files_from_events(events: list[Any]) -> list[str]:
    payload = _latest_model_authoring_payload(events)
    files = payload.get("model_contributed_files")
    if isinstance(files, list):
        return [str(item) for item in files]
    return []


def model_contributed_to_authoring(events: list[Any]) -> bool:
    contributed = model_contributed_files_from_events(events)
    return (
        _REQUIRED_AGENT_PATH in contributed
        and any(path.endswith(_REQUIRED_TEST_SUFFIX) for path in contributed)
        and "generated/author_output_contract.json" in contributed
    )


def load_reference_scaffold(root: Path | None) -> dict[str, str]:
    if root is None or not root.is_dir():
        return {}
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
            continue
        if "data" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        generated_rel = f"generated/{rel}"
        files[generated_rel] = path.read_text(encoding="utf-8")
    return files


_BUNDLED_BANK_REFERENCE_TEMPLATE = "bank_categoriser"
_BUNDLED_BANK_REFERENCE_FILENAME = "bank_transaction_categorisation_demo.csv"
_BUNDLED_BANK_REFERENCE_GOLDEN_REPO_REL = (
    "apps/web/app/api/reference-samples/bank-categoriser/expected_output.csv"
)
_BUNDLED_BANK_REFERENCE_GOLDEN_WORKSPACE_REL = "evals/golden_output.csv"
_BUNDLED_BANK_REFERENCE_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "date",
    "account",
    "description",
    "counterparty",
    "amount",
    "currency",
    "reference",
    "direction",
)
_BUNDLED_BANK_REFERENCE_REQUIRED_OUTPUT_COLUMNS: tuple[str, ...] = (
    "category",
    "rule_matched",
    "rule_used",
    "confidence_score",
    "review_required",
)
_BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES: tuple[str, ...] = (
    "Revenue",
    "Payroll",
    "Software",
    "Bank Fees",
    "Travel",
    "Rent",
    "Tax",
    "Office Supplies",
    "Other",
)
_BUNDLED_BANK_REFERENCE_EXPECTED_CATEGORY_COUNTS: dict[str, int] = {
    "Revenue": 3,
    "Payroll": 1,
    "Software": 4,
    "Bank Fees": 1,
    "Travel": 3,
    "Rent": 1,
    "Tax": 1,
    "Office Supplies": 3,
    "Other": 1,
}
_EXPENSE_EXCEPTION_REVIEW_TEMPLATE = "expense_exception_review"
_EXPENSE_EXCEPTION_GOLDEN_REPO_REL = (
    "evals/golden/expense_exception_review/expected_output.csv"
)
_EXPENSE_EXCEPTION_GOLDEN_WORKSPACE_REL = "evals/expected_output.csv"
_EXPENSE_EXCEPTION_CORE_COLUMNS = frozenset(
    {"amount", "policy_limit", "receipt_attached", "approval_status", "notes"}
)
_EXPENSE_RECEIPT_COLUMN_HINTS = frozenset({"receipt_attached", "receipt_available"})
_EXPENSE_APPROVAL_COLUMN_HINTS = frozenset(
    {"approval_status", "manager_approved", "status"}
)
_EXPENSE_NOTES_COLUMN_HINTS = frozenset({"notes", "description", "memo"})
_EXPENSE_SUSPICIOUS_NOTE_TERMS = (
    "personal",
    "duplicate",
    "missing receipt",
    "cash advance",
)
_BUNDLED_BANK_REFERENCE_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Revenue",
        (
            "stripe payout",
            "client transfer",
            "client receipt",
            "customer receipt",
            "invoice payment",
        ),
    ),
    ("Payroll", ("payroll", "salary", "staff payroll")),
    (
        "Software",
        (
            "aws",
            "microsoft",
            "github",
            "adobe",
            "saas",
            "cloud services",
            "software subscription",
            "subscription",
        ),
    ),
    ("Bank Fees", ("service charge", "bank fee", "account fee", "barclays")),
    (
        "Travel",
        ("trainline", "uber", "lyft", "airline", "airways", "flight", "hotel", "taxi", "travel"),
    ),
    ("Rent", ("wework", "office rent", "workspace rent")),
    ("Tax", ("hmrc", "vat", "corporation tax", "tax payment")),
    (
        "Office Supplies",
        (
            "amazon business",
            "office chairs",
            "office furniture",
            "ikea",
            "stationery",
            "office supplies",
        ),
    ),
)
_BUNDLED_BANK_REFERENCE_WARNING = (
    "Bundled reference sample contract scaffold used for demo stability; generated "
    "agent code and tests were still model-authored and validated."
)
_BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS: tuple[dict[str, str], ...] = (
    {
        "transaction_id": "BTX-0001",
        "description": "STRIPE PAYOUT MAY 01",
        "expected_category": "Revenue",
        "reason": "Stripe payouts are Revenue.",
    },
    {
        "transaction_id": "BTX-0002",
        "description": "CLIENT TRANSFER INV-8821",
        "expected_category": "Revenue",
        "reason": "Client transfers and customer receipts are Revenue.",
    },
    {
        "transaction_id": "BTX-0003",
        "description": "PAYROLL MAY RUN",
        "expected_category": "Payroll",
        "reason": "Payroll runs are Payroll.",
    },
    {
        "transaction_id": "BTX-0004",
        "description": "AWS EMEA CLOUD SERVICES",
        "expected_category": "Software",
        "reason": "AWS cloud services are Software.",
    },
    {
        "transaction_id": "BTX-0005",
        "description": "MICROSOFT 365 BUSINESS PLAN",
        "expected_category": "Software",
        "reason": "Microsoft 365 is Software.",
    },
    {
        "transaction_id": "BTX-0006",
        "description": "GITHUB PRO SUBSCRIPTION",
        "expected_category": "Software",
        "reason": "GitHub subscriptions are Software.",
    },
    {
        "transaction_id": "BTX-0007",
        "description": "BARCLAYS SERVICE CHARGE",
        "expected_category": "Bank Fees",
        "reason": "Bank service charges are Bank Fees.",
    },
    {
        "transaction_id": "BTX-0008",
        "description": "TRAINLINE BUSINESS TRAVEL",
        "expected_category": "Travel",
        "reason": "Trainline travel is Travel.",
    },
    {
        "transaction_id": "BTX-0009",
        "description": "UBER TRIP TO CLIENT SITE",
        "expected_category": "Travel",
        "reason": "Uber travel is Travel.",
    },
    {
        "transaction_id": "BTX-0010",
        "description": "BRITISH AIRWAYS LHR-MAD",
        "expected_category": "Travel",
        "reason": "Airline travel is Travel.",
    },
    {
        "transaction_id": "BTX-0011",
        "description": "WEWORK OFFICE RENT MAY",
        "expected_category": "Rent",
        "reason": "WeWork office rent is Rent.",
    },
    {
        "transaction_id": "BTX-0012",
        "description": "HMRC VAT PAYMENT",
        "expected_category": "Tax",
        "reason": "HMRC VAT payments are Tax.",
    },
    {
        "transaction_id": "BTX-0013",
        "description": "AMAZON BUSINESS OFFICE CHAIRS",
        "expected_category": "Office Supplies",
        "reason": "Amazon Business office purchases are Office Supplies.",
    },
    {
        "transaction_id": "BTX-0014",
        "description": "IKEA OFFICE FURNITURE DELIVERY",
        "expected_category": "Office Supplies",
        "reason": "IKEA office furniture is Office Supplies.",
    },
    {
        "transaction_id": "BTX-0015",
        "description": "OFFICE SUPPLIES ROUTINE",
        "expected_category": "Office Supplies",
        "reason": "Routine office supplies are Office Supplies.",
    },
    {
        "transaction_id": "BTX-0016",
        "description": "ADOBE CREATIVE CLOUD",
        "expected_category": "Software",
        "reason": "Adobe subscriptions are Software.",
    },
    {
        "transaction_id": "BTX-0017",
        "description": "CLIENT RECEIPT INV-8840",
        "expected_category": "Revenue",
        "reason": "Positive incoming customer receipts are Revenue.",
    },
    {
        "transaction_id": "BTX-0018",
        "description": "UNKNOWN CARD PURCHASE 9912",
        "expected_category": "Other",
        "reason": "Unknown unmatched merchant should be Other with low confidence and review_required true.",
    },
)


def stage_bundled_bank_reference_golden(*, workspace: Path) -> bool:
    """Stage the committed bank demo expected output for Layer 5 validation."""
    from agentforge.evals.scenarios import resolve_repo_path

    src = resolve_repo_path(_BUNDLED_BANK_REFERENCE_GOLDEN_REPO_REL)
    if not src.is_file():
        return False
    dest = workspace / _BUNDLED_BANK_REFERENCE_GOLDEN_WORKSPACE_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def stage_expense_exception_golden(*, workspace: Path) -> bool:
    """Stage the committed expense demo expected output for Layer 5 validation."""
    from agentforge.evals.scenarios import resolve_repo_path

    src = resolve_repo_path(_EXPENSE_EXCEPTION_GOLDEN_REPO_REL)
    if not src.is_file():
        return False
    dest = workspace / _EXPENSE_EXCEPTION_GOLDEN_WORKSPACE_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def apply_bundled_bank_reference_golden_policy(
    contract: AuthorOutputContract,
) -> AuthorOutputContract:
    """Ensure bundled bank demo contracts require independent golden comparison."""
    skipped_checks = [
        check for check in contract.skipped_checks if check != "golden_output"
    ]
    return contract.model_copy(
        update={
            "golden_comparison_requirement": "required",
            "golden_output_path": _BUNDLED_BANK_REFERENCE_GOLDEN_WORKSPACE_REL,
            "skipped_checks": skipped_checks,
        }
    )


_EXPENSE_EXCEPTION_REQUIRED_OUTPUT_COLUMNS: tuple[str, ...] = (
    "expense_id",
    "exception_flag",
    "review_required",
)


def _expense_exception_review_required_semantics() -> dict[str, Any]:
    return {
        "name": "review_required",
        "description": "Whether manual review is required for this expense row.",
        "producer_kind": "exception_flag",
        "required": True,
        "nullable": False,
        "allow_empty_string": False,
        "row_semantics": (
            "Emit 'yes' when any exception rule fires on the row; otherwise emit 'no'."
        ),
        "fallback_value_semantics": "Default to 'no' when no exception rule matches.",
    }


def _expense_exception_flag_semantics() -> dict[str, Any]:
    return {
        "name": "exception_flag",
        "description": "Flag indicating whether any policy exception rule fired for the row",
        "producer_kind": "exception_flag",
        "required": True,
        "nullable": False,
        "allow_empty_string": False,
        "row_semantics": (
            "Set to 'yes' if any of the defined exception rules evaluate to true; otherwise 'no'."
        ),
        "fallback_value_semantics": "Default to 'no' when no exception conditions match.",
    }


def _expense_exception_flagged_enum_value(contract: AuthorOutputContract) -> str:
    allowed = (contract.allowed_enums or {}).get("exception_flag") or ["yes", "no"]
    non_flagged = {
        "no",
        "no_issue",
        "no_exception",
        "false",
        "0",
        "n",
        "clean",
        "ok",
        "none",
        "pass",
        "passed",
    }
    for value in allowed:
        if value.lower() not in non_flagged:
            return value
    return "yes"


def _expense_exception_reason_semantics() -> dict[str, Any]:
    return {
        "name": "exception_reason",
        "description": "Human-readable reason when an exception rule fires.",
        "producer_kind": "rule_explanation",
        "required": False,
        "nullable": True,
        "allow_empty_string": True,
        "row_semantics": (
            "Populate with the reason from matching exception rules; leave empty when "
            "exception_flag is 'no'."
        ),
        "fallback_value_semantics": "Leave empty when no exception rule matches.",
    }


def _normalize_expense_exception_output_columns(
    output_columns: list[str],
) -> list[str]:
    columns = list(output_columns)
    if "review_required" in columns:
        return columns
    insert_at = len(columns)
    if "exception_reason" in columns:
        insert_at = columns.index("exception_reason") + 1
    elif "exception_flag" in columns:
        insert_at = columns.index("exception_flag") + 1
    columns.insert(insert_at, "review_required")
    return columns


def _normalize_expense_exception_required_columns(
    *,
    required_output_columns: list[str],
    output_columns: list[str],
) -> list[str]:
    normalized: list[str] = []
    for column in _EXPENSE_EXCEPTION_REQUIRED_OUTPUT_COLUMNS:
        if column in output_columns and column not in normalized:
            normalized.append(column)
    for column in required_output_columns:
        lowered = column.strip().lower()
        if lowered == "exception_reason":
            continue
        if column in output_columns and column not in normalized:
            normalized.append(column)
    return normalized


def _normalize_expense_exception_output_column_semantics(
    contract: AuthorOutputContract,
    *,
    output_columns: list[str],
    required_output_columns: list[str],
) -> list[OutputColumnSemanticsSpec]:
    required_names = set(required_output_columns)
    by_name: dict[str, OutputColumnSemanticsSpec] = {
        spec.name: spec for spec in contract.output_column_semantics
    }
    for column in output_columns:
        if column not in by_name:
            by_name[column] = OutputColumnSemanticsSpec.model_validate(
                _passthrough_output_column_semantics_spec(column)
            )
    by_name["review_required"] = OutputColumnSemanticsSpec.model_validate(
        _expense_exception_review_required_semantics()
    )
    by_name["exception_flag"] = OutputColumnSemanticsSpec.model_validate(
        _expense_exception_flag_semantics()
    )
    by_name["exception_reason"] = OutputColumnSemanticsSpec.model_validate(
        _expense_exception_reason_semantics()
    )
    for name, spec in list(by_name.items()):
        by_name[name] = spec.model_copy(update={"required": name in required_names})
    return [by_name[column] for column in output_columns if column in by_name]


def _normalize_expense_exception_validation_checks(
    validation_checks: list[ValidationCheckSpec],
) -> list[ValidationCheckSpec]:
    normalized: list[ValidationCheckSpec] = []
    for check in validation_checks:
        payload = check.model_dump(mode="json")
        formula = str(payload.get("formula") or "")
        output_column = str(payload.get("output_column") or "")
        if output_column == "exception_flag" or "exception_flag" in formula:
            lowered = formula.lower()
            if "no_issue" in lowered or (
                "review_required" in lowered and output_column == "exception_flag"
            ):
                payload["formula"] = "exception_flag IN ['yes', 'no']"
                payload["name"] = "exception_flag must be yes or no"
        normalized.append(ValidationCheckSpec.model_validate(payload))
    return normalized


def normalize_expense_exception_contract(
    contract: AuthorOutputContract,
) -> AuthorOutputContract:
    """Align expense exception contracts with golden review_required expectations."""
    skipped_checks = [
        check for check in contract.skipped_checks if check != "golden_output"
    ]
    output_columns = _normalize_expense_exception_output_columns(
        list(contract.output_columns)
    )
    required_output_columns = _normalize_expense_exception_required_columns(
        required_output_columns=list(contract.required_output_columns),
        output_columns=output_columns,
    )
    allowed_enums = dict(contract.allowed_enums or {})
    allowed_enums["exception_flag"] = ["yes", "no"]
    allowed_enums.setdefault("review_required", ["yes", "no"])
    output_column_semantics = _normalize_expense_exception_output_column_semantics(
        contract,
        output_columns=output_columns,
        required_output_columns=required_output_columns,
    )
    validation_checks = _normalize_expense_exception_validation_checks(
        list(contract.validation_checks)
    )
    updates: dict[str, object] = {
        "golden_comparison_requirement": "required",
        "golden_output_path": _EXPENSE_EXCEPTION_GOLDEN_WORKSPACE_REL,
        "skipped_checks": skipped_checks,
        "output_columns": output_columns,
        "required_output_columns": required_output_columns,
        "allowed_enums": allowed_enums,
        "output_column_semantics": output_column_semantics,
        "validation_checks": validation_checks,
    }
    if not contract.primary_row_key:
        updates["primary_row_key"] = "expense_id"
    if contract.workflow_type in {
        "model_authored_finance_workflow",
        "finance_exception_review",
    }:
        updates["workflow_type"] = "expense_exception_review"
    return contract.model_copy(update=updates)


def apply_expense_exception_golden_policy(
    contract: AuthorOutputContract,
) -> AuthorOutputContract:
    """Ensure expense exception demo contracts require golden comparison and review_required."""
    return normalize_expense_exception_contract(contract)


def _schema_column_names(schema_profile: dict[str, Any]) -> set[str]:
    return {
        str(column).strip().lower()
        for column in (schema_profile.get("columns") or [])
        if str(column).strip()
    }


def _is_expense_exception_schema(schema_profile: dict[str, Any]) -> bool:
    """Detect expense exception review uploads from column shape only."""
    columns = _schema_column_names(schema_profile)
    if not columns:
        return False
    has_amount = "amount" in columns
    has_limit = "policy_limit" in columns
    has_receipt = bool(columns & _EXPENSE_RECEIPT_COLUMN_HINTS)
    has_approval = bool(columns & _EXPENSE_APPROVAL_COLUMN_HINTS)
    has_notes = bool(columns & _EXPENSE_NOTES_COLUMN_HINTS)
    has_row_key = bool(columns & {"expense_id"})
    return (
        has_amount
        and has_limit
        and has_receipt
        and has_approval
        and (has_notes or has_row_key)
    )


def _is_expense_exception_review_context(
    *,
    template_hint: str | None,
    schema_profile: dict[str, Any],
) -> bool:
    if template_hint == _EXPENSE_EXCEPTION_REVIEW_TEMPLATE:
        return True
    return _is_expense_exception_schema(schema_profile)


def _expense_exception_schema_guidance(*, mode: str) -> dict[str, Any]:
    planning_rules = [
        "When schema_profile includes amount, policy_limit, receipt (receipt_attached "
        "or receipt_available), approval_status (or manager_approved), and notes/description "
        "columns, treat the upload as an expense exception review workflow.",
        "Infer candidate exception_rules from the schema and concise review prompt. The "
        "model still authors the contract; the backend does not classify rows.",
        "Candidate rules to consider when columns are present:",
        "  - amount_over_limit: amount > policy_limit (severity high).",
        "  - missing_receipt: receipt is false/no/0 and the expense should carry a receipt.",
        "  - approval_not_final: approval_status is not approved (pending, rejected, etc.).",
        "  - suspicious_notes: notes/description contains personal, duplicate, cash advance, "
        "or missing receipt language.",
        "When the user asks to flag policy exceptions, include exception_flag, "
        "exception_reason, review_required, severity, and rule_used in output columns.",
        "Include review_required in required_output_columns with allowed_enums yes/no on "
        "every row; golden validation compares review_required only.",
        "Use allowed_enums exception_flag yes/no on every row; set exception_flag=yes "
        "when any exception rule fires and no otherwise.",
        "Do not require exception_reason to be non-null on clean rows (exception_flag=no); "
        "leave exception_reason empty when no exception rule fires.",
        "Populate review_required with yes when any exception rule fires and no otherwise.",
        "Do not hardcode row counts or specific expense_id values from sample_rows.",
        "Derive allowed_enums and validation_checks from the authored rules; do not "
        "encode sample-row outcomes directly in the contract.",
    ]
    review_rules = [
        "During review, verify every inferred expense exception rule is represented in "
        "exception_rules with a machine-readable condition and human-readable reason.",
        "Ensure approval_status and notes-based rules are present when the schema includes "
        "those columns and the user prompt requests policy exception review.",
        "When notes/description is present, include a suspicious_notes (or equivalent) "
        "exception_rules entry with machine-readable keyword conditions so rule_used and "
        "review_required semantics stay aligned with finance review expectations.",
        "Ensure review_required semantics match exception_flag behaviour.",
        "Ensure review_required is listed in required_output_columns and output_columns.",
        "Ensure exception_reason is optional on clean rows (nullable/allow_empty_string).",
    ]
    if mode == "review":
        return {"planning_rules": planning_rules, "review_rules": review_rules}
    return {"planning_rules": planning_rules}


def _is_bundled_bank_reference_sample(
    *,
    template_hint: str | None,
    schema_profile: dict[str, Any],
) -> bool:
    if template_hint != _BUNDLED_BANK_REFERENCE_TEMPLATE:
        return False
    columns = list(schema_profile.get("columns") or [])
    if columns != list(_BUNDLED_BANK_REFERENCE_COLUMNS):
        return False
    if int(schema_profile.get("row_count") or 0) != 18:
        return False
    input_path = str(
        schema_profile.get("normalized_input_path")
        or schema_profile.get("agent_input_path")
        or schema_profile.get("input_file")
        or ""
    )
    if not input_path:
        return False
    return Path(input_path).name == _BUNDLED_BANK_REFERENCE_FILENAME


def _is_bundled_bank_reference_contract(
    *,
    template_hint: str | None,
    contract: AuthorOutputContract,
) -> bool:
    if template_hint != _BUNDLED_BANK_REFERENCE_TEMPLATE:
        return False
    if list(contract.input_columns) != list(_BUNDLED_BANK_REFERENCE_COLUMNS):
        return False
    return Path(contract.input_file).name == _BUNDLED_BANK_REFERENCE_FILENAME


def _bank_reference_scaffold_enabled(settings: Settings | None) -> bool:
    return bool(settings and settings.author_enable_bank_reference_scaffold)


def _bundled_bank_reference_required_output_semantics() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        _passthrough_output_column_semantics_spec(column)
        for column in _BUNDLED_BANK_REFERENCE_COLUMNS
    ]
    specs.extend(
        [
            {
                "name": "category",
                "description": "Finance review category assigned from explicit bank demo rules.",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": (
                    "Assign exactly one category from the bundled demo category list "
                    "for every transaction row."
                ),
                "fallback_value_semantics": (
                    "Use Other only when no explicit keyword or counterparty rule "
                    "matches; do not leave category blank."
                ),
            },
            {
                "name": "rule_matched",
                "description": "Human-readable explanation of the keyword or logic that matched.",
                "producer_kind": "rule_explanation",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": (
                    "Populate a concrete explanation of the keyword, counterparty, or "
                    "rule match used to assign the category."
                ),
                "fallback_value_semantics": (
                    "When category is Other, explain that no explicit rule matched "
                    "and therefore manual review is required."
                ),
            },
            {
                "name": "rule_used",
                "description": "Rule label or explanation for the categorisation logic used.",
                "producer_kind": "rule_explanation",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": (
                    "Populate a concrete rule label or explanation for the logic used "
                    "to assign the category."
                ),
                "fallback_value_semantics": (
                    "When rule_matched and rule_used are the same explanation, return "
                    "the same non-empty text twice rather than leaving one blank."
                ),
            },
            {
                "name": "confidence_score",
                "description": "Numeric confidence score for the assigned category.",
                "producer_kind": "other",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": (
                    "Populate a numeric confidence score between 0 and 1 for every "
                    "transaction row."
                ),
                "fallback_value_semantics": (
                    "Use a concrete low-confidence value below 0.70 for Other rows "
                    "that need review; never leave the score blank."
                ),
            },
            {
                "name": "review_required",
                "description": "Boolean-like flag indicating whether human review is required.",
                "producer_kind": "exception_flag",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": (
                    "Populate a boolean-like true/false review flag for every "
                    "transaction row."
                ),
                "fallback_value_semantics": (
                    "Set review_required to true for Other rows and false for clear "
                    "high-confidence keyword matches."
                ),
            },
        ]
    )
    return specs


def _bundled_bank_reference_contract_scaffold(
    *,
    schema_profile: dict[str, Any],
) -> AuthorOutputContract:
    input_file = str(
        schema_profile.get("normalized_input_path")
        or schema_profile.get("agent_input_path")
        or f"uploads/{_BUNDLED_BANK_REFERENCE_FILENAME}"
    )
    payload: dict[str, Any] = {
        "workflow_type": "bank_transaction_categorisation",
        "workflow_confidence": 1.0,
        "build_mode": "llm_custom",
        "input_file": input_file,
        "input_format": "csv",
        "normalized_input_path": input_file,
        "row_level_output_file": "outputs/output.csv",
        "summary_output_files": [],
        "exception_output_files": [],
        "primary_row_key": "transaction_id",
        "input_columns": list(_BUNDLED_BANK_REFERENCE_COLUMNS),
        "output_columns": list(_BUNDLED_BANK_REFERENCE_COLUMNS)
        + list(_BUNDLED_BANK_REFERENCE_REQUIRED_OUTPUT_COLUMNS),
        "required_output_columns": list(_BUNDLED_BANK_REFERENCE_REQUIRED_OUTPUT_COLUMNS),
        "optional_output_columns": [],
        "output_column_semantics": _bundled_bank_reference_required_output_semantics(),
        "calculated_fields": [],
        "formula_input_columns": ["amount"],
        "formula_output_columns": [],
        "tolerances": {},
        "aggregation_specs": [],
        "summary_group_keys": ["category"],
        "summary_metrics": [
            {
                "name": "transaction_count",
                "metric_type": "count",
                "group_by": ["category"],
                "description": "Transaction count by category for the validation report.",
            },
            {
                "name": "total_debits",
                "metric_type": "sum",
                "source_column": "amount",
                "group_by": ["category"],
                "filter": "direction == 'debit'",
                "description": "Total debit amount by category for the validation report.",
            },
            {
                "name": "total_credits",
                "metric_type": "sum",
                "source_column": "amount",
                "group_by": ["category"],
                "filter": "direction == 'credit'",
                "description": "Total credit amount by category for the validation report.",
            },
            {
                "name": "net_amount",
                "metric_type": "sum",
                "source_column": "amount",
                "group_by": ["category"],
                "description": "Net signed amount by category for the validation report.",
            },
        ],
        "exception_rules": [],
        "classification_rules": [
            {
                "name": "revenue_rule",
                "description": "Stripe payouts, client transfers, and customer receipts map to Revenue.",
            },
            {
                "name": "payroll_rule",
                "description": "Payroll runs and staff payroll descriptions map to Payroll.",
            },
            {
                "name": "software_rule",
                "description": "AWS, Microsoft, GitHub, Adobe, SaaS, and cloud-service subscriptions map to Software.",
            },
            {
                "name": "bank_fees_rule",
                "description": "Bank service charges and account fees map to Bank Fees.",
            },
            {
                "name": "travel_rule",
                "description": "Trainline, Uber, airlines, flights, hotels, taxis, and travel bookings map to Travel.",
            },
            {
                "name": "rent_rule",
                "description": "WeWork and office/workspace rent map to Rent.",
            },
            {
                "name": "tax_rule",
                "description": "HMRC, VAT, corporation tax, and tax payments map to Tax.",
            },
            {
                "name": "office_supplies_rule",
                "description": "Amazon Business office purchases, office furniture, IKEA office items, and stationery map to Office Supplies.",
            },
            {
                "name": "other_rule",
                "description": "Use Other only when no explicit rule matches; Other rows must have low confidence and review_required true.",
            },
        ],
        "validation_checks": [
            {
                "check_id": "row_count_preserved",
                "layer": "row_level",
                "name": "Preserve one output row per input row",
                "required": True,
                "check_type": "row_count",
            },
            {
                "check_id": "confidence_score_range",
                "layer": "business_rules",
                "name": "Confidence score stays within 0 to 1",
                "required": True,
                "check_type": "formula",
                "formula": "0 <= confidence_score and confidence_score <= 1",
                "input_columns": ["confidence_score"],
                "output_column": "confidence_score",
            },
        ],
        "golden_comparison_requirement": "required",
        "golden_output_path": _BUNDLED_BANK_REFERENCE_GOLDEN_WORKSPACE_REL,
        "skipped_checks": [],
        "warnings": [_BUNDLED_BANK_REFERENCE_WARNING],
        "clarification_questions": [],
        "unsupported_reasons": [],
        "requested_deliverables": [
            {
                "name": "workflow_report",
                "description": "Validation report explaining rules, category counts, uncertain rows, and summary totals.",
                "output_path": "reports/validation_report.md",
                "required": True,
                "source": "platform_canonical",
            }
        ],
        "produced_deliverables": [],
        "missing_deliverables": [],
        "preserve_row_count": True,
        "allowed_enums": {
            "category": list(_BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES),
        },
    }
    return AuthorOutputContract.model_validate(payload)


def _bundled_bank_reference_contract_scaffold_payload(
    *,
    schema_profile: dict[str, Any],
) -> dict[str, Any]:
    return _bundled_bank_reference_contract_scaffold(
        schema_profile=schema_profile
    ).model_dump(mode="json")


def _bundled_bank_reference_codegen_section() -> str:
    lines = [
        "Bundled bank reference sample constraints:",
        "- This run is the built-in 18-row bank transaction categorisation demo.",
        f"- Input columns are exactly: {', '.join(_BUNDLED_BANK_REFERENCE_COLUMNS)}.",
        f"- Allowed categories are exactly: {', '.join(_BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES)}.",
        "- Implement the classifier with one normalized lowercase haystack built from description, counterparty, and reference.",
        "- Match lowercase keywords against that lowercase haystack. Do not compare lowercase keywords against .upper() text or uppercase keywords against .lower() text.",
        "- categorize_transaction (or any equivalent helper) must always return exactly five values in this order:",
        "  category, rule_matched, rule_used, confidence_score, review_required",
        "- Every return branch must return five values. Do not unpack five variables from a helper that returns four.",
        "- If rule_matched and rule_used use the same explanation, return the same non-empty explanatory string twice rather than omitting one value.",
        "- outputs/output.csv must contain 18 data rows, not a header-only file.",
        "- reports/validation_report.md must be non-empty and must include category summary evidence.",
        "- Preserve every original input row and original input column.",
        "- For Other rows, set confidence_score below 0.70 and review_required to true.",
        "- Do not classify obvious software, travel, rent, tax, office-supplies, payroll, or revenue rows as Other.",
        "- Recommended keyword groups for the normalized haystack:",
    ]
    for category, keywords in _BUNDLED_BANK_REFERENCE_KEYWORD_RULES:
        lines.append(f"  - {category}: {', '.join(keywords)}")
    lines.extend(
        [
            "- Suggested implementation sketch for the classifier:",
            "  - haystack = ' '.join([description, counterparty, reference]).lower()",
            "  - Revenue if any of: stripe payout, client transfer, client receipt, customer receipt, invoice payment",
            "  - Payroll if any of: payroll, salary, staff payroll",
            "  - Software if any of: aws, microsoft, github, adobe, saas, cloud services, software subscription, subscription",
            "  - Bank Fees if any of: service charge, bank fee, account fee, barclays",
            "  - Travel if any of: trainline, uber, lyft, airline, airways, flight, hotel, taxi, travel",
            "  - Rent if any of: wework, office rent, workspace rent",
            "  - Tax if any of: hmrc, vat, corporation tax, tax payment",
            "  - Office Supplies if any of: amazon business, office chairs, office furniture, ikea, stationery, office supplies",
            "  - Otherwise return Other with low confidence and review_required true",
            "- Exact demo category counts derived from the required row mappings are:",
            "  Revenue=3, Payroll=1, Software=4, Bank Fees=1, Travel=3, Rent=1, Tax=1, Office Supplies=3, Other=1",
            "- If you include report summaries or internal checks, they must match those exact counts.",
            "- Required demo row expectations:",
        ]
    )
    for item in _BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS:
        lines.append(
            f"  - {item['transaction_id']} / {item['description']} -> {item['expected_category']} ({item['reason']})"
        )
    return "\n".join(lines)



def _bundled_bank_reference_test_generation_requirements() -> list[str]:
    requirements = [
        "This bundled bank reference sample has exactly 18 input rows, so assert that outputs/output.csv has exactly 18 data rows.",
        "Assert that every output row preserves the original input columns and also includes category, rule_matched, rule_used, confidence_score, and review_required.",
        f"Assert that category values are drawn only from: {', '.join(_BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES)}.",
        "Assert that reports/validation_report.md exists and is non-empty.",
        "Assert that the Other row has confidence_score below 0.70 and review_required true.",
        "Prefer exact row assertions keyed by transaction_id over invented aggregate heuristics.",
        "If you assert category counts, use the exact demo counts: Revenue=3, Payroll=1, Software=4, Bank Fees=1, Travel=3, Rent=1, Tax=1, Office Supplies=3, Other=1.",
        "Add contract-backed sample assertions for the obvious reference rows below; these are bundled demo expectations, not generic finance heuristics.",
    ]
    for item in _BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS:
        requirements.append(
            f"For transaction_id {item['transaction_id']} ({item['description']}), assert category == {item['expected_category']!r}."
        )
    return requirements


def _bundled_bank_reference_repair_requirements() -> list[str]:
    requirements = [
        "This failure happened on the bundled 18-row bank transaction categorisation reference sample.",
        "Preserve the contract scaffold categories exactly and do not drift to alternative labels.",
        "The repaired agent must still produce 18 output data rows and a non-empty validation report.",
        "If a categorisation helper returns multiple values, every branch must return category, rule_matched, rule_used, confidence_score, review_required in that exact order.",
        "Do not remove required output columns or collapse rule_matched/rule_used to a missing value.",
        "Repair the classifier by using one lowercase haystack from description, counterparty, and reference, then match lowercase keywords against it.",
        "Known keyword groups for the bundled sample are: Revenue=stripe payout/client transfer/client receipt/invoice payment; Payroll=payroll/salary; Software=aws/microsoft/github/adobe/saas/cloud services/subscription; Bank Fees=service charge/bank fee/account fee/barclays; Travel=trainline/uber/lyft/airline/airways/flight/hotel/taxi/travel; Rent=wework/office rent/workspace rent; Tax=hmrc/vat/corporation tax/tax payment; Office Supplies=amazon business/office chairs/office furniture/ikea/stationery/office supplies.",
        "If you update report summaries or category-count assertions, use the exact demo counts Revenue=3, Payroll=1, Software=4, Bank Fees=1, Travel=3, Rent=1, Tax=1, Office Supplies=3, Other=1.",
        "Known demo row expectations to preserve:",
    ]
    for item in _BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS:
        requirements.append(
            f"  - {item['transaction_id']} / {item['description']} -> {item['expected_category']}"
        )
    return requirements


_BANK_RULE_SEMANTIC_CATEGORY_PREFS: dict[str, tuple[str, ...]] = {
    "Revenue": ("Income", "Revenue"),
    "Payroll": ("Payroll", "Income"),
    "Software": ("Subscriptions", "Software"),
    "Bank Fees": ("Bank Fees", "Office Expense"),
    "Travel": ("Travel",),
    "Rent": ("Rent", "Office Expense"),
    "Tax": ("Tax",),
    "Office Supplies": ("Office Expense", "Office Supplies"),
}
_BANK_REFUND_CATEGORY_PREFS: tuple[str, ...] = ("Refund",)
_BANK_FALLBACK_CATEGORY_PREFS: tuple[str, ...] = ("Uncategorised", "Other")
_CUSTOM_BANK_CATEGORISER_WARNING = (
    "Custom bank transaction categorisation guidance applied; keyword rules were "
    "mapped to the contract category list. Generated agent code is still "
    "model-authored and validated."
)


def bank_categoriser_template_root() -> Path | None:
    from agentforge.evals.scenarios import resolve_repo_path

    root = resolve_repo_path("templates/bank_categoriser")
    return root if root.is_dir() else None


def _is_bank_transaction_schema(schema_profile: dict[str, Any]) -> bool:
    cols = {
        str(column).strip().lower()
        for column in (schema_profile.get("columns") or [])
        if str(column).strip()
    }
    bundled_cols = {column.lower() for column in _BUNDLED_BANK_REFERENCE_COLUMNS}
    if bundled_cols <= cols:
        return True
    return {"txn_id", "account", "amount"}.issubset(cols) and bool(
        cols & {"description", "counterparty"}
    )


def _is_custom_bank_categoriser_sample(
    *,
    template_hint: str | None,
    schema_profile: dict[str, Any],
) -> bool:
    if template_hint != _BUNDLED_BANK_REFERENCE_TEMPLATE:
        return False
    if _is_bundled_bank_reference_sample(
        template_hint=template_hint,
        schema_profile=schema_profile,
    ):
        return False
    return _is_bank_transaction_schema(schema_profile)


def _normalise_allowed_categories(contract: AuthorOutputContract) -> tuple[str, ...]:
    raw = (contract.allowed_enums or {}).get("category") or []
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _pick_allowed_category(
    preferences: tuple[str, ...],
    allowed: tuple[str, ...],
) -> str | None:
    if not allowed:
        return None
    by_lower = {category.lower(): category for category in allowed}
    for pref in preferences:
        if pref in allowed:
            return pref
        matched = by_lower.get(pref.lower())
        if matched:
            return matched
    return None


def _is_custom_bank_categoriser_contract(
    *,
    template_hint: str | None,
    contract: AuthorOutputContract,
) -> bool:
    if template_hint != _BUNDLED_BANK_REFERENCE_TEMPLATE:
        return False
    if _is_bundled_bank_reference_contract(
        template_hint=template_hint,
        contract=contract,
    ):
        return False
    return bool(_normalise_allowed_categories(contract))


def _resolve_custom_bank_category_mapping(
    allowed: tuple[str, ...],
) -> list[tuple[str, str, tuple[str, ...]]]:
    mappings: list[tuple[str, str, tuple[str, ...]]] = []
    for semantic, keywords in _BUNDLED_BANK_REFERENCE_KEYWORD_RULES:
        prefs = _BANK_RULE_SEMANTIC_CATEGORY_PREFS.get(semantic, (semantic,))
        resolved = _pick_allowed_category(prefs, allowed)
        if resolved and keywords:
            mappings.append((semantic, resolved, keywords))
    return mappings


def _custom_bank_categoriser_haystack_fields(schema_profile: dict[str, Any]) -> tuple[str, ...]:
    cols = {
        str(column).strip().lower()
        for column in (schema_profile.get("columns") or [])
        if str(column).strip()
    }
    fields: list[str] = []
    for candidate in ("description", "counterparty", "reference"):
        if candidate in cols:
            fields.append(candidate)
    return tuple(fields) or ("description", "counterparty")


def _custom_bank_categoriser_codegen_section(
    *,
    reviewed_contract: AuthorOutputContract,
    schema_profile: dict[str, Any],
) -> str:
    allowed = _normalise_allowed_categories(reviewed_contract)
    fallback = _pick_allowed_category(_BANK_FALLBACK_CATEGORY_PREFS, allowed) or (
        allowed[-1] if allowed else "Uncategorised"
    )
    refund_category = _pick_allowed_category(_BANK_REFUND_CATEGORY_PREFS, allowed)
    haystack_fields = _custom_bank_categoriser_haystack_fields(schema_profile)
    haystack_expr = " + ' ' + ".join(
        f"str(row.get({field!r}) or '')" for field in haystack_fields
    )
    lines = [
        "Custom bank transaction categorisation constraints:",
        "- This run uses a custom bank-transaction CSV, not the bundled 18-row demo.",
        f"- Allowed categories from the contract are exactly: {', '.join(allowed) or '(see allowed_enums.category)'}.",
        "- Use the contract category labels verbatim; do not invent alternate labels.",
        "- Map common finance synonyms to the allowed list when needed:",
        "  Revenue or client receipts -> Income when Income is allowed, otherwise Revenue.",
        "  Software, SaaS, or cloud vendors -> Subscriptions when allowed, otherwise Software.",
        "  Payroll or salary runs -> Payroll when allowed, otherwise Income for outbound payroll debits.",
        "  Bank service charges -> Bank Fees when allowed.",
        "  Routine office purchases -> Office Expense or Office Supplies when those are allowed.",
        "  Truly unknown merchants -> Uncategorised or Other when that is the explicit fallback category.",
        "- Implement the classifier with one normalized lowercase haystack built from "
        f"{', '.join(haystack_fields)}.",
        f"- Build haystack as: ({haystack_expr}).strip().lower()",
        "- Match lowercase keywords against that lowercase haystack. Do not compare "
        "lowercase keywords against .upper() text or uppercase keywords against .lower() text.",
    ]
    if refund_category:
        lines.append(
            f"- Refund-first override: when amount < 0, assign {refund_category!r} "
            "before any keyword rule."
        )
    lines.append("- Recommended keyword groups mapped to this contract:")
    for _semantic, resolved, keywords in _resolve_custom_bank_category_mapping(allowed):
        lines.append(f"  - {resolved}: {', '.join(keywords)}")
    unmapped: list[str] = []
    for semantic, keywords in _BUNDLED_BANK_REFERENCE_KEYWORD_RULES:
        prefs = _BANK_RULE_SEMANTIC_CATEGORY_PREFS.get(semantic, (semantic,))
        resolved = _pick_allowed_category(prefs, allowed)
        if not resolved and keywords:
            unmapped.append(f"  - {semantic}: {', '.join(keywords)}")
    if unmapped:
        lines.append(
            "- These keyword groups do not have a matching allowed category; map them to the "
            "closest allowed label instead of "
            f"{fallback!r}:"
        )
        lines.extend(unmapped)
    lines.extend(
        [
            "- Do not leave obvious payroll, AWS/cloud, bank fee, HMRC/VAT, client transfer, "
            "Stripe payout, travel, or office-supply rows in the fallback category.",
            f"- Only use {fallback!r} when no keyword rule clearly applies.",
            "- Preserve every original input row and original input column.",
            "- Populate rule_matched (and rule_used when required) with the keyword or logic matched.",
        ]
    )
    return "\n".join(lines)


def _classification_test_generation_requirements(
    reviewed_contract: AuthorOutputContract,
) -> list[str]:
    requirements: list[str] = []
    has_confidence = "confidence_score" in reviewed_contract.required_output_columns
    has_rule_matched = "rule_matched" in reviewed_contract.required_output_columns
    clear_rule_check = next(
        (
            check
            for check in reviewed_contract.validation_checks or []
            if check.required
            and check.check_id == "clear_rule_confidence_08"
            and check.formula
        ),
        None,
    )
    if clear_rule_check and has_confidence and has_rule_matched:
        requirements.extend(
            [
                "When asserting validation_checks.clear_rule_confidence_08, mirror the "
                "contract formula exactly: a row satisfies the check when rule_matched is "
                "empty/null OR confidence_score >= 0.80.",
                "Do not treat every non-empty rule_matched as a clear keyword match. "
                "Fallback explanations such as 'No rule matched', 'No match', or similar "
                "no-rule notes still indicate no clear keyword rule even though the column "
                "is non-empty.",
                "Prefer category == 'Other' (or the contract fallback category) OR empty "
                "rule_matched as the no-clear-match condition before asserting the >= 0.80 "
                "confidence rule.",
            ]
        )
    if reviewed_contract.summary_metrics:
        requirements.extend(
            [
                "When asserting validation report summary evidence, verify numeric or "
                "category summary content using normalized human-readable headings "
                "(for example transaction_count -> Transaction Count) or by computing "
                "metrics from outputs/output.csv and checking matching values in the report.",
                "Summary metric names in the contract are internal identifiers unless the "
                "contract explicitly requires exact literal wording; do not assert raw "
                "snake_case metric identifier strings as report text by default.",
                "Do not require every allowed_enums.category label to appear in the report "
                "when no output row uses that category unless the contract explicitly "
                "requires zero-row categories to be listed.",
                "Derive expected report categories from outputs/output.csv (or the contract "
                "primary_row_key grouped counts) rather than iterating the full allowed enum "
                "list by default.",
            ]
        )
    return requirements


def _custom_bank_categoriser_test_generation_requirements(
    *,
    reviewed_contract: AuthorOutputContract,
    schema_profile: dict[str, Any],
) -> list[str]:
    allowed = _normalise_allowed_categories(reviewed_contract)
    row_count = int(schema_profile.get("row_count") or 0)
    requirements = [
        "This is a custom bank-transaction categorisation upload, not the bundled 18-row demo.",
        "Assert that category values are drawn only from the contract allowed_enums.category list.",
        "Prefer contract-backed row assertions keyed by the contract primary_row_key when sample rows are obvious.",
        "Do not assert the bundled demo category counts or BTX-* transaction ids.",
        "If the contract requires rule_matched or rule_used, assert they are non-empty for categorised rows.",
    ]
    if row_count > 0:
        requirements.insert(
            1,
            f"Assert that outputs/output.csv has exactly {row_count} data rows.",
        )
    if allowed:
        requirements.append(
            "When asserting keyword-driven rows, use the contract category labels "
            f"({', '.join(allowed)}), not bundled demo labels such as Revenue or Software."
        )
    fallback = _pick_allowed_category(_BANK_FALLBACK_CATEGORY_PREFS, allowed)
    if fallback:
        requirements.append(
            f"Allow at most a small number of rows in {fallback!r}; do not expect most rows "
            "to fall back when obvious vendor keywords are present in the input."
        )
    return requirements


def _custom_bank_categoriser_repair_requirements(
    *,
    contract: AuthorOutputContract,
    schema_profile: dict[str, Any],
) -> list[str]:
    allowed = _normalise_allowed_categories(contract)
    requirements = [
        "This failure happened on a custom bank-transaction categorisation upload.",
        "Preserve the contract allowed_enums.category labels exactly.",
        "Repair the classifier by using one lowercase haystack from description, "
        f"{'/'.join(_custom_bank_categoriser_haystack_fields(schema_profile))}, "
        "then match lowercase keywords against it.",
        "Do not drift to bundled demo labels such as Revenue or Software unless they "
        "appear in allowed_enums.category.",
        "Do not classify obvious payroll, AWS/cloud, bank fee, HMRC/VAT, client transfer, "
        "Stripe payout, travel, or office-supply rows as the fallback category.",
    ]
    for _semantic, resolved, keywords in _resolve_custom_bank_category_mapping(allowed):
        requirements.append(f"{resolved} keywords include: {', '.join(keywords)}")
    return requirements


def _extract_fenced_json_candidate(text: str) -> tuple[str | None, list[str]]:
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if not fence:
        return None, []
    return fence.group(1).strip(), ["markdown_fence_stripping"]


def _skip_json_whitespace_and_comments(text: str, index: int) -> tuple[int, bool, bool]:
    line_comment_removed = False
    block_comment_removed = False
    length = len(text)
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if char.isspace():
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment_removed = True
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and nxt == "*":
            block_comment_removed = True
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            if index + 1 < length:
                index += 2
            else:
                index = length
            continue
        break
    return index, line_comment_removed, block_comment_removed


def _clean_json_formatting_artifacts(text: str) -> tuple[str, list[str]]:
    stripped = text.strip()
    if not stripped:
        return stripped, []
    out: list[str] = []
    in_string = False
    escaped = False
    line_comment_removed = False
    block_comment_removed = False
    trailing_comma_removed = False
    index = 0
    length = len(stripped)
    while index < length:
        char = stripped[index]
        nxt = stripped[index + 1] if index + 1 < length else ""
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment_removed = True
            index += 2
            while index < length and stripped[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and nxt == "*":
            block_comment_removed = True
            index += 2
            while index + 1 < length and not (
                stripped[index] == "*" and stripped[index + 1] == "/"
            ):
                index += 1
            if index + 1 < length:
                index += 2
            else:
                index = length
            continue
        if char == ",":
            next_index, skipped_line_comment, skipped_block_comment = (
                _skip_json_whitespace_and_comments(stripped, index + 1)
            )
            line_comment_removed = line_comment_removed or skipped_line_comment
            block_comment_removed = block_comment_removed or skipped_block_comment
            if next_index < length and stripped[next_index] in "}]":
                trailing_comma_removed = True
                index += 1
                continue
        out.append(char)
        index += 1

    classes: list[str] = []
    if line_comment_removed:
        classes.append("outside_string_line_comment_removal")
    if block_comment_removed:
        classes.append("outside_string_block_comment_removal")
    if trailing_comma_removed:
        classes.append("trailing_comma_removal")
    return "".join(out).strip(), classes


def _extract_json_payload_with_hygiene(
    text: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    stripped = text.strip()
    if not stripped:
        return None, []

    candidates: list[tuple[str, list[str]]] = [(stripped, [])]
    fenced, fence_classes = _extract_fenced_json_candidate(text)
    if fenced and fenced != stripped:
        candidates.append((fenced, fence_classes))

    seen: set[str] = set()
    expanded: list[tuple[str, list[str]]] = []
    for candidate, classes in candidates:
        if candidate not in seen:
            seen.add(candidate)
            expanded.append((candidate, classes))
        cleaned, cleaned_classes = _clean_json_formatting_artifacts(candidate)
        if cleaned and cleaned not in seen and cleaned != candidate:
            seen.add(cleaned)
            expanded.append((cleaned, classes + cleaned_classes))

    for candidate, classes in expanded:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, classes
    return None, []


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    payload, _classes = _extract_json_payload_with_hygiene(text)
    return payload


def _normalize_agent_path_literal(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _match_forbidden_write_path(value: str) -> str | None:
    normalized = _normalize_agent_path_literal(value)
    if normalized in _BACKEND_MANAGED_ARTIFACT_PATHS:
        return normalized
    for prefix in _GENERATED_AGENT_FORBIDDEN_WRITE_PREFIXES:
        if normalized.startswith(prefix):
            return normalized
    base = normalized.rsplit("/", 1)[-1]
    if base in _BACKEND_MANAGED_ARTIFACT_PATHS and normalized == base:
        return base
    return None


def _forbidden_write_path_label(path: str) -> str:
    return f"write to reserved path: {path}"


def _ast_str_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _open_call_write_mode(call: ast.Call) -> bool:
    mode: str | None = None
    if len(call.args) >= 2:
        mode = _ast_str_constant(call.args[1])
    if mode is None:
        for keyword in call.keywords:
            if keyword.arg == "mode":
                mode = _ast_str_constant(keyword.value)
                break
    if mode is None:
        return False
    return mode in _WRITE_OPEN_MODES or mode.replace("+", "").replace("b", "") in {"w", "a", "x"}


def _scan_reserved_path_writes_regex(content: str) -> list[str]:
    issues: list[str] = []
    for pattern, path_hint in _RESERVED_PATH_WRITE_REGEXES:
        if pattern.search(content):
            if path_hint == "reserved path write":
                for match in pattern.finditer(content):
                    captured = match.group(1)
                    if captured:
                        issues.append(_forbidden_write_path_label(captured))
            else:
                issues.append(_forbidden_write_path_label(path_hint))
    return issues


def _scan_reserved_path_writes(content: str) -> list[str]:
    issues: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return sorted(set(_scan_reserved_path_writes_regex(content)))

    var_reserved: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        literal = _ast_str_constant(node.value)
        if literal is None:
            continue
        forbidden = _match_forbidden_write_path(literal)
        if forbidden is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                var_reserved[target.id] = forbidden

    def resolve_path_arg(arg: ast.AST | None) -> str | None:
        if arg is None:
            return None
        literal = _ast_str_constant(arg)
        if literal is not None:
            return _match_forbidden_write_path(literal)
        if isinstance(arg, ast.Name):
            return var_reserved.get(arg.id)
        if isinstance(arg, ast.JoinedStr):
            for value in arg.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    matched = _match_forbidden_write_path(value.value)
                    if matched:
                        return matched
        return None

    def resolve_path_from_call(value: ast.AST | None) -> str | None:
        matched = resolve_path_arg(value)
        if matched:
            return matched
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id in {"Path", "pathlib.Path"} and value.args:
                return resolve_path_arg(value.args[0])
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            (isinstance(func, ast.Name) and func.id == "open")
            or (isinstance(func, ast.Attribute) and func.attr == "open")
        ) and _open_call_write_mode(node):
            matched = resolve_path_arg(node.args[0] if node.args else None)
            if matched:
                issues.append(_forbidden_write_path_label(matched))
            continue
        if isinstance(func, ast.Attribute) and func.attr in {"write_text", "write_bytes"}:
            matched = resolve_path_from_call(func.value)
            if matched:
                issues.append(_forbidden_write_path_label(matched))
            continue
        if isinstance(func, ast.Name) and func.id == "ZipFile" and len(node.args) >= 2:
            mode = _ast_str_constant(node.args[1])
            if mode in {"w", "a", "x"}:
                matched = resolve_path_arg(node.args[0])
                if matched:
                    issues.append(_forbidden_write_path_label(matched))
            continue
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "ZipFile"
            and len(node.args) >= 2
        ):
            mode = _ast_str_constant(node.args[1])
            if mode in {"w", "a", "x"}:
                matched = resolve_path_arg(node.args[0])
                if matched:
                    issues.append(_forbidden_write_path_label(matched))

    issues.extend(_scan_reserved_path_writes_regex(content))
    return sorted(set(issues))


def _scan_model_code(content: str) -> list[str]:
    issues: list[str] = []
    for pattern, label in _UNSAFE_CODE_PATTERNS:
        if pattern.search(content):
            issues.append(label)
    issues.extend(_scan_reserved_path_writes(content))
    return sorted(set(issues))


def _forbidden_construct_labels() -> list[str]:
    labels: list[str] = []
    for _pattern, label in _UNSAFE_CODE_PATTERNS:
        if label not in labels:
            labels.append(label)
    return labels


def _validate_generated_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    if not normalized.startswith("generated/"):
        return False
    if ".." in normalized.split("/"):
        return False
    return normalized.endswith(".py")


def _record_model_call(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    response: Any,
    purpose: str,
    contributed_files: list[str] | None = None,
    settings: Settings | None = None,
    attempt: int | None = None,
    duration_ms: int | None = None,
) -> None:
    event_client = _client_for_purpose(
        model_client=model_client,
        purpose=purpose,
        settings=settings,
    )
    metadata = getattr(event_client, "metadata", {}) or {}
    usage = response.usage.model_dump(mode="json")
    usage["total_tokens"] = response.usage.total_tokens
    configured_max_tokens = _codegen_max_tokens_for_purpose(purpose, settings)
    output_tokens = int(response.usage.output_tokens or 0)
    hit_configured_max_tokens = (
        configured_max_tokens is not None and output_tokens >= configured_max_tokens
    )
    near_configured_max_tokens = (
        configured_max_tokens is not None
        and output_tokens >= max(1, int(configured_max_tokens * 0.98))
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.MODEL_CALLED,
        actor_type=ActorType.MODEL,
        payload={
            "stage": stage_label,
            "purpose": purpose,
            "provider": metadata.get("provider", "unknown"),
            "model": metadata.get("model", "unknown"),
            "base_url": metadata.get("base_url", ""),
            "configured_max_tokens": configured_max_tokens,
            "hit_configured_max_tokens": hit_configured_max_tokens,
            "near_configured_max_tokens": near_configured_max_tokens,
            "stop_reason": getattr(response, "stop_reason", None),
            "usage": usage,
            "model_contributed_files": list(contributed_files or []),
            **({"attempt": attempt} if attempt is not None else {}),
            **({"duration_ms": duration_ms} if duration_ms is not None else {}),
        },
        step=step,
    )
    from agentforge.persistence.live_budget_sync import persist_live_session_budget

    persist_live_session_budget(session_id=session_id, event_log=event_log)


def _json_prompt(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


_STAGE_MAX_TOKENS: dict[str, int] = {
    "contract_planning": 4096,
    "contract_review": 4096,
    "contract_revision": 4096,
    "contract_schema_repair": 4096,
    "json_repair": 4096,
    "test_generation": 4096,
    "missing_file_repair": 4096,
    "safety_repair": 2048,
    "author_file_repair": 4096,
}

_CODEGEN_PURPOSES = frozenset(
    {
        "code_generation",
        "code_adaptation",
        "test_generation",
        "validation_check_generation",
        "missing_file_repair",
        "safety_repair",
        "author_file_repair",
    }
)

_AGENT_SOURCE_MARKERS: tuple[str, ...] = ("import ", "from ", "def ", "class ", "#!/")

_PLANNING_PURPOSES = frozenset(
    {
        "contract_planning",
        "contract_review",
        "contract_revision",
        "contract_schema_repair",
        "json_repair",
    }
)

_CODEGEN_PURPOSES = frozenset({"code_generation", "code_adaptation"})
_TESTGEN_PURPOSES = frozenset({"test_generation", "validation_check_generation"})
_REPAIR_PURPOSES = frozenset(
    {
        "missing_file_repair",
        "missing_files_repair",
        "safety_repair",
        "author_file_repair",
        "execution_repair",
        "code_repair",
        "repair_iteration",
    }
)

_HEARTBEAT_INTERVAL_SECONDS = 30

_FORMULA_DSL_DESCRIPTION = (
    "supported arithmetic DSL using column names, numeric literals, and +, -, *, / only"
)


def _compact_schema_profile(schema_profile: dict[str, Any]) -> dict[str, Any]:
    from agentforge.orchestrator.author_date_clarification import (
        date_clarification_prompt_section,
    )
    column_profiles: list[dict[str, Any]] = []
    for profile in schema_profile.get("column_profiles") or []:
        if not isinstance(profile, dict):
            continue
        compact: dict[str, Any] = {
            "name": profile.get("name"),
            "inferred_type": profile.get("inferred_type"),
        }
        sample_values = profile.get("sample_values")
        if isinstance(sample_values, list) and sample_values:
            compact["sample_values"] = sample_values[:2]
        column_profiles.append(compact)
    sample_rows = schema_profile.get("sample_rows")
    compact_rows = sample_rows[:3] if isinstance(sample_rows, list) else []
    compact: dict[str, Any] = {
        "columns": schema_profile.get("columns"),
        "row_count": schema_profile.get("row_count"),
        "upload_format": schema_profile.get("upload_format"),
        "selected_sheet": schema_profile.get("selected_sheet"),
        "normalized_input_path": schema_profile.get("normalized_input_path"),
        "agent_input_path": schema_profile.get("agent_input_path"),
        "sample_rows": compact_rows,
        "column_profiles": column_profiles,
        "candidate_id_columns": schema_profile.get("candidate_id_columns"),
        "candidate_grouping_columns": schema_profile.get("candidate_grouping_columns"),
        "candidate_date_columns": schema_profile.get("candidate_date_columns"),
        "candidate_amount_columns": schema_profile.get("candidate_amount_columns"),
        "candidate_status_columns": schema_profile.get("candidate_status_columns"),
        "candidate_category_columns": schema_profile.get("candidate_category_columns"),
        "candidate_reference_columns": schema_profile.get("candidate_reference_columns"),
    }
    clarification_section = date_clarification_prompt_section(
        schema_profile.get("date_format_clarification")
        if isinstance(schema_profile.get("date_format_clarification"), dict)
        else None
    )
    compact.update(clarification_section)
    return compact


_CODEGEN_CONTRACT_KEYS: tuple[str, ...] = (
    "workflow_type",
    "build_mode",
    "input_file",
    "input_format",
    "selected_sheet",
    "normalized_input_path",
    "row_level_output_file",
    "summary_output_files",
    "exception_output_files",
    "primary_row_key",
    "input_columns",
    "output_columns",
    "required_output_columns",
    "optional_output_columns",
    "output_column_semantics",
    "calculated_fields",
    "formula_input_columns",
    "formula_output_columns",
    "tolerances",
    "aggregation_specs",
    "summary_group_keys",
    "summary_metrics",
    "exception_rules",
    "classification_rules",
    "validation_checks",
    "requested_deliverables",
    "preserve_row_count",
    "allowed_enums",
)


def _compact_codegen_contract(contract: AuthorOutputContract) -> dict[str, Any]:
    """Compact validated contract payload for codegen/test prompts."""
    raw = contract.model_dump(mode="json")
    compact: dict[str, Any] = {}
    for key in _CODEGEN_CONTRACT_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            continue
        if isinstance(value, list | dict) and not value:
            continue
        compact[key] = value
    return compact


def _is_small_author_workflow(
    schema_profile: dict[str, Any],
    settings: Settings | None,
) -> bool:
    threshold = 25
    if settings is not None:
        threshold = int(settings.author_small_workflow_row_threshold)
    row_count = schema_profile.get("row_count")
    return isinstance(row_count, int) and row_count <= threshold


def _use_compact_contract_prompts(
    schema_profile: dict[str, Any],
    settings: Settings | None,
) -> bool:
    if settings is not None and not settings.author_compact_contract_prompts:
        return False
    return _is_small_author_workflow(schema_profile, settings)


def _contract_prompt_rules_core() -> list[str]:
    """Essential schema rules for review/repair after planning already ran."""
    return [
        "Return JSON only. No markdown fences or prose outside JSON.",
        "Use exact AuthorOutputContract keys only. No extra keys.",
        (
            'build_mode must be exactly one of "llm_custom", "clarification", or '
            '"unsupported". Do not invent build_mode values.'
        ),
        (
            "Echo required_file_profile_fields exactly for input_file, input_format, "
            "and selected_sheet when present."
        ),
        "input_columns must come from schema_profile; do not invent raw source columns.",
        (
            "Never nest allowed_enums, allowed_values, categories, min_value, max_value, "
            "or enum_values inside validation_checks."
        ),
        (
            "Use top-level allowed_enums for category membership and formula-based "
            "validation_checks for numeric ranges."
        ),
        (
            "Every required_output_columns entry needs matching output_column_semantics "
            "with required=true, nullable=false, allow_empty_string=false, row_semantics, "
            "and fallback_value_semantics."
        ),
        (
            "Non-arithmetic derived outputs such as *_date, *_status, *_reason, "
            "*_bucket, *_category, *_flag, *_ready, and *_required must use "
            "output_column_semantics rather than calculated_fields.formula."
        ),
        "Do not emit output_format, preserve_original_data, enable_logging, or enable_debugging.",
        "Structured DeliverableSpec objects must use output_path, not path.",
        "Structured deliverables use output_path, not path.",
        (
            "Preserve valid business logic and make the smallest correction needed for "
            "the listed validation issues or review defects."
        ),
    ]


def _contract_stage_guidance(
    *,
    mode: str,
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared contract-stage guidance with mode-specific trimming."""
    guidance: dict[str, Any] = {
        "allowed_schema_types": _allowed_schema_types(),
        "calculated_field_guidance": _calculated_field_guidance(),
        "exception_rule_guidance": _exception_rule_guidance(),
        "validation_check_guidance": _validation_check_guidance(),
        "allowed_enums_guidance": _allowed_enums_guidance(),
        "artifact_path_namespace_guidance": _artifact_path_namespace_guidance(),
        "contract_return_shape_guidance": _contract_return_shape_guidance(),
        "output_column_semantics_guidance": _output_column_semantics_guidance(),
        "contract_schema_dialect_guidance": _contract_schema_dialect_guidance(),
    }
    if schema_profile and _is_expense_exception_schema(schema_profile):
        guidance["expense_exception_schema_guidance"] = _expense_exception_schema_guidance(
            mode=mode
        )
    if mode == "planning":
        guidance["exact_schema_example"] = _contract_schema_example()
        guidance["summary_metric_guidance"] = _summary_metric_guidance()
        guidance["requested_deliverables_guidance"] = _requested_deliverables_guidance()
        guidance["rules"] = _contract_prompt_rules()
        return guidance
    guidance["rules"] = _contract_prompt_rules_core()
    guidance["rules_reference"] = (
        "Apply the same AuthorOutputContract schema constraints established during "
        "contract planning. Refer to stage guidance objects for field shapes."
    )
    if mode == "review":
        guidance["summary_metric_guidance"] = _summary_metric_guidance()
        guidance["requested_deliverables_guidance"] = _requested_deliverables_guidance()
        return guidance
    if mode == "schema_repair":
        guidance["summary_metric_guidance"] = _summary_metric_guidance()
        guidance["requested_deliverables_guidance"] = _requested_deliverables_guidance()
    return guidance


def _contract_for_prompt(
    contract: AuthorOutputContract | dict[str, Any],
    *,
    compact: bool,
) -> dict[str, Any]:
    if isinstance(contract, dict):
        return contract
    if compact:
        return _compact_codegen_contract(contract)
    return contract.model_dump(mode="json")


def _compact_output_contract_summary(contract: AuthorOutputContract) -> dict[str, Any]:
    return {
        "required_output_paths": contract.all_required_output_paths(),
        "required_output_columns": contract.required_output_columns,
        "preserve_row_count": contract.preserve_row_count,
        "required_artifacts": _required_artifact_specs(contract),
    }


def _compact_repair_artifact_context(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> dict[str, Any]:
    context = _artifact_context_for_repair(workspace=workspace, contract=contract)
    compact: dict[str, Any] = {
        "missing_required_artifact_paths": context.get("missing_required_artifact_paths", []),
        "produced_required_artifact_paths": context.get("produced_required_artifact_paths", []),
    }
    if context.get("row_output_header"):
        compact["row_output_header"] = context["row_output_header"]
    if context.get("row_output_sample_rows"):
        compact["row_output_sample_rows"] = context["row_output_sample_rows"]
    if context.get("report_excerpt"):
        compact["report_excerpt"] = context["report_excerpt"]
    return compact


def _compact_pytest_repair_requirements() -> list[str]:
    return [
        *_generated_test_workspace_path_requirements(),
        "Return one complete corrected generated/tests/test_agent.py file.",
        "Preserve the explicit CLI interface and current artifact paths.",
        "Use csv.DictReader for CSV assertions; never treat the header row as data.",
        "Preserve working tests that already pass; fix only failing assertions.",
        (
            "If failing assertions show Category missing from report, enum mismatch, or "
            "obvious keyword rows classified as Other, repair generated/agent.py rather "
            "than weakening the test."
        ),
        (
            "If the only failing assertion treats rule_matched='No rule matched' as "
            "requiring confidence >= 0.80, repair the test to mirror "
            "validation_checks.clear_rule_confidence_08."
        ),
        (
            "For expense exception review, remove exact exception_reason prose equality on "
            "production rows; check non-empty required fields and stable exception_flag, "
            "review_required, severity, and rule_used instead."
        ),
        (
            "For expense synthetic tests, read exceptions from the contract "
            "exception_output_files path; split rule_used on ';' for multi-rule rows."
        ),
        (
            "Do not modify generated/agent.py unless the failing pytest proves an agent "
            "contract violation."
        ),
        "Every assertion must be traceable to the reviewed contract or required artifacts.",
    ]


def _compact_repair_failure_detail(failure_detail: str, *, max_chars: int = 2500) -> str:
    lines = failure_detail.splitlines()
    keep: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(
            marker in stripped
            for marker in (
                "FAILED ",
                "AssertionError",
                "assert ",
                "E       ",
                "pytest summary",
                "failed /",
                "failing_tests=",
                "failure_kind=",
            )
        ):
            keep.append(stripped)
    if keep:
        excerpt = "\n".join(keep)
        return excerpt[-max_chars:]
    return failure_detail[-max_chars:]


def _repair_failure_signature(failure_detail: str) -> str:
    markers: list[str] = []
    for line in failure_detail.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            markers.append(stripped.split(" - ", 1)[0])
        elif "AssertionError" in stripped:
            markers.append(stripped[:240])
    if not markers:
        return failure_detail.strip()[-400:]
    return "\n".join(_dedupe_preserving_order(markers))


def _record_stage_prompt_metrics(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    purpose: str,
    prompt: str,
    compact_mode: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "kind": "author_prompt_metrics",
        "stage": stage_label,
        "purpose": purpose,
        "prompt_chars": len(prompt),
        "prompt_lines": prompt.count("\n") + (1 if prompt else 0),
        "compact_mode": compact_mode,
    }
    if extra:
        payload.update(extra)
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload=payload,
        step=step,
    )


def _use_compact_execution_repair_prompts(
    *,
    schema_profile: dict[str, Any],
    settings: Settings | None,
    failure_kind: str,
) -> bool:
    if failure_kind != "pytest":
        return False
    return _use_compact_contract_prompts(schema_profile, settings)


def _timeout_seconds_for_purpose(purpose: str, settings: Settings) -> float:
    """Return the global request timeout for all Author purposes."""
    del purpose
    provider = settings.llm_provider.strip().lower()
    if provider == "deepseek":
        return float(settings.deepseek_timeout_seconds)
    return float(settings.ollama_timeout_seconds)


def _llm_provider_name(settings: Settings | None) -> str | None:
    if settings is None:
        return None
    return settings.llm_provider.strip().lower()


def _configured_timeout_seconds(
    client: ModelClient,
    settings: Settings | None,
    *,
    purpose: str | None = None,
) -> float | None:
    provider = _llm_provider_name(settings)
    if (
        settings is not None
        and purpose is not None
        and provider in {"ollama", "deepseek"}
    ):
        return _timeout_seconds_for_purpose(purpose, settings)
    timeout = getattr(client, "_timeout", None)
    if isinstance(timeout, int | float):
        return float(timeout)
    if settings is not None and provider == "ollama":
        return float(settings.ollama_timeout_seconds)
    if settings is not None and provider == "deepseek":
        return float(settings.deepseek_timeout_seconds)
    return None


def _model_call_failure_detail(
    *,
    purpose: str,
    exc: ModelClientError,
    elapsed_seconds: int,
    timeout_seconds: float | None,
    client: ModelClient,
) -> str:
    metadata = getattr(client, "metadata", {}) or {}
    message = str(exc)
    is_timeout = "timed out" in message.lower() or "timeout" in message.lower()
    parts = [
        f"purpose={purpose}",
        f"elapsed_seconds={elapsed_seconds}",
    ]
    if timeout_seconds is not None:
        parts.append(f"configured_timeout_seconds={int(timeout_seconds)}")
    provider = metadata.get("provider")
    if provider:
        parts.append(f"provider={provider}")
    model = metadata.get("model")
    if model:
        parts.append(f"model={model}")
    if is_timeout:
        parts.append("failure=timeout")
    parts.append(f"error={message}")
    return "; ".join(parts)


def _primary_report_path(contract: AuthorOutputContract) -> str:
    for entry in contract.requested_deliverables:
        path = deliverable_output_path(entry) or deliverable_label(entry)
        if deliverable_required(entry) and path.lower().endswith(".md"):
            return path
    for entry in contract.requested_deliverables:
        path = deliverable_output_path(entry) or deliverable_label(entry)
        if path.lower().endswith(".md"):
            return path
    for entry in contract.summary_output_files:
        path = output_file_path(entry)
        if path.lower().endswith(".md"):
            return path
    return "reports/validation_report.md"


def generated_agent_cli_args(
    *,
    input_path: str,
    contract_path: str,
    contract: AuthorOutputContract,
) -> list[str]:
    return [
        "--input",
        input_path,
        "--contract",
        contract_path,
        "--row-output",
        contract.row_level_output_file,
        "--report-path",
        _primary_report_path(contract),
    ]


def generated_agent_cli_command(
    *,
    input_path: str,
    contract_path: str,
    contract: AuthorOutputContract,
) -> str:
    argv = ["python", "generated/agent.py", *generated_agent_cli_args(
        input_path=input_path,
        contract_path=contract_path,
        contract=contract,
    )]
    return shlex.join(argv)


def _generated_agent_cli_contract(contract: AuthorOutputContract) -> dict[str, Any]:
    return {
        "production_command": generated_agent_cli_command(
            input_path=contract.normalized_input_path or contract.input_file,
            contract_path="generated/author_output_contract.json",
            contract=contract,
        ),
        "arguments": {
            "--input": contract.normalized_input_path or contract.input_file,
            "--contract": "generated/author_output_contract.json",
            "--row-output": contract.row_level_output_file,
            "--report-path": _primary_report_path(contract),
        },
        "semantics": {
            "--input": "Read the uploaded normalized input file from this path.",
            "--contract": (
                "Read-only AuthorOutputContract JSON input. Never write CSV rows or "
                "reports to this path."
            ),
            "--row-output": (
                "Exact required row-level CSV artifact path. Write the output CSV here."
            ),
            "--report-path": (
                "Exact required validation report path. Write the markdown report here."
            ),
        },
        "requirements": [
            "Read the contract JSON to respect any additional summary or exception outputs.",
            "Inspect the contract for every required output path before you return success.",
            "Do not stop after writing only the row-level output CSV when the contract also requires summary or exception artifacts.",
            "Do not treat the contract path as an output CSV path.",
            "Preserve all original input columns in the row-level output CSV.",
            "Create every required artifact before exiting 0.",
            "If a required CSV artifact has zero data rows, still create it with the required header row.",
            "If the contract requires an exception CSV, write it to the exact required path even when no rows match; include the required header row.",
            "If the contract requires a summary CSV, compute it from the contract's summary_group_keys and summary_metrics and write it to the exact required path.",
            "Populate every required output column for every row; required columns must never be null, blank, or missing.",
            "Never read, delete, or write a generated output or exception column on a row unless you assigned it for that row; keep helper flags local unless the contract output schema explicitly includes them.",
            "Never perform arithmetic directly on raw CSV/XLSX cell values; coerce numeric fields with safe_decimal before arithmetic.",
            "Coerce date fields with parse_date before aging buckets or date comparisons.",
            "Exit non-zero if required outputs cannot be produced.",
        ],
    }


def _required_artifact_specs(contract: AuthorOutputContract) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    summary_by_path = {
        output_file_path(entry): entry for entry in contract.summary_output_files
    }
    exception_by_path = {
        output_file_path(entry): entry for entry in contract.exception_output_files
    }
    deliverables_by_path: dict[str, Any] = {}
    for entry in contract.requested_deliverables:
        path = deliverable_output_path(entry)
        if path and deliverable_required(entry):
            deliverables_by_path[path] = entry
    for path in contract.all_required_output_paths():
        if path == contract.row_level_output_file:
            specs.append(
                {
                    "path": path,
                    "artifact_type": "row_output_csv",
                    "required_columns": list(contract.output_columns),
                    "required_non_null_columns": list(contract.required_output_columns),
                    "preserve_row_count": contract.preserve_row_count,
                    "create_header_when_empty": True,
                }
            )
            continue
        if path in summary_by_path:
            entry = summary_by_path[path]
            if path.lower().endswith(".md"):
                specs.append(
                    {
                        "path": path,
                        "artifact_type": "markdown_report",
                        "required_sections": list(
                            getattr(entry, "required_columns", None)
                            if isinstance(entry, OutputFileSpec)
                            else []
                        ),
                        "must_be_non_empty": True,
                    }
                )
            else:
                specs.append(
                    {
                        "path": path,
                        "artifact_type": "summary_csv",
                        "required_columns": list(
                            getattr(entry, "required_columns", None)
                            if isinstance(entry, OutputFileSpec)
                            else []
                        ),
                        "summary_group_keys": list(contract.summary_group_keys),
                        "summary_metrics": [
                            metric
                            if isinstance(metric, str)
                            else metric.model_dump(mode="json")
                            for metric in contract.summary_metrics
                        ],
                        "create_header_when_empty": True,
                    }
                )
            continue
        if path in exception_by_path:
            entry = exception_by_path[path]
            specs.append(
                {
                    "path": path,
                    "artifact_type": "exception_csv",
                    "required_columns": list(
                        getattr(entry, "required_columns", None)
                        if isinstance(entry, OutputFileSpec)
                        else []
                    ),
                    "create_header_when_empty": True,
                }
            )
            continue
        if path in deliverables_by_path:
            if path.lower().endswith(".md"):
                specs.append(
                    {
                        "path": path,
                        "artifact_type": "markdown_report",
                        "required_sections": [],
                        "must_be_non_empty": True,
                    }
                )
            elif path.lower().endswith(".csv"):
                specs.append(
                    {
                        "path": path,
                        "artifact_type": "generic_csv",
                        "required_columns": [],
                        "create_header_when_empty": True,
                    }
                )
    return specs


def _optional_artifact_paths(contract: AuthorOutputContract) -> list[str]:
    required = set(contract.all_required_output_paths())
    return [path for path in contract.all_declared_output_paths() if path not in required]


def _required_artifacts_section(*, reviewed_contract: AuthorOutputContract) -> str:
    lines = ["Required artifacts:"]
    for spec in _required_artifact_specs(reviewed_contract):
        path = spec["path"]
        artifact_type = spec["artifact_type"]
        if artifact_type == "markdown_report":
            sections = ", ".join(spec.get("required_sections") or [])
            detail = f"non-empty markdown report"
            if sections:
                detail += f"; must cover: {sections}"
            lines.append(f"- {path}: {detail}.")
            continue
        columns = ", ".join(spec.get("required_columns") or [])
        detail = f"CSV with header columns: {columns}" if columns else "CSV artifact"
        lines.append(f"- {path}: {detail}.")
        if spec.get("create_header_when_empty"):
            lines.append(
                "  Create this file even when there are zero matching rows; write the header row."
            )
            if artifact_type == "exception_csv":
                lines.append(
                    "  If no rows match, still write the required exception CSV with headers only."
                )
        if spec.get("preserve_row_count"):
            lines.append("  Preserve one data row per input row.")
        if artifact_type == "summary_csv":
            group_keys = ", ".join(spec.get("summary_group_keys") or [])
            metric_names = ", ".join(
                metric if isinstance(metric, str) else metric.get("name", "")
                for metric in (spec.get("summary_metrics") or [])
                if (metric if isinstance(metric, str) else metric.get("name"))
            )
            lines.append(
                "  Compute and write this required summary using the contract's "
                "summary_group_keys and summary_metrics; do not skip it just because "
                "the row-level output exists."
            )
            if group_keys:
                lines.append(f"  Group by: {group_keys}.")
            if metric_names:
                lines.append(f"  Summary metrics to write: {metric_names}.")
        non_null = ", ".join(spec.get("required_non_null_columns") or [])
        if non_null:
            lines.append(
                f"  Populate these required columns for every row and never leave them blank or null: {non_null}."
            )
    lines.append("- Do not exit 0 until every required artifact above exists at the exact path.")
    lines.append(
        "- The row-level output alone is not sufficient when other required artifacts are listed above."
    )
    optional_paths = _optional_artifact_paths(reviewed_contract)
    if optional_paths:
        lines.append(
            "- Declared but optional artifacts: "
            + ", ".join(optional_paths)
            + ". Do not treat them as required for a successful run unless you choose to implement them."
        )
    return "\n".join(lines)


def _report_section_requirements_section(*, reviewed_contract: AuthorOutputContract) -> str:
    report_path = _primary_report_path(reviewed_contract)
    lines = [
        "Report section requirements (satisfy inside the required report, not as separate files):",
        f"- Primary report path: {report_path}",
    ]
    for entry in reviewed_contract.summary_output_files:
        path = output_file_path(entry)
        if path.lower().endswith(".md") and path == report_path:
            sections = (
                getattr(entry, "required_columns", None)
                if isinstance(entry, OutputFileSpec)
                else []
            ) or []
            for section in sections:
                lines.append(f"- Include report section: {section}")
    for metric in reviewed_contract.summary_metrics:
        if isinstance(metric, str):
            lines.append(f"- Include summary metric in report: {metric}")
        else:
            label = metric.description or metric.name
            lines.append(f"- Include summary metric in report: {metric.name} ({label})")
    for rule in reviewed_contract.exception_rules:
        if isinstance(rule, str):
            lines.append(f"- Mention exception/review rule in report: {rule}")
        else:
            lines.append(
                f"- Mention rows matching {rule.name} in report: {rule.reason}"
            )
    lines.append(
        "- The workflow report must explain applied rules, category or status counts, "
        "uncertain rows, and rows flagged for human review when the contract requires them."
    )
    lines.append(
        "- User-facing workflow reports must use workspace-relative paths only "
        "(for example uploads/input.csv, outputs/output.csv, outputs/exceptions.csv). "
        "Never embed absolute filesystem paths or machine-specific directories."
    )
    lines.append(
        "- If CLI args may be absolute paths, normalize report references to "
        "workspace-relative contract paths instead of echoing raw argparse values."
    )
    lines.append(
        "- Do not create separate required CSV files for report sections unless they "
        "appear in Required artifacts above."
    )
    return "\n".join(lines)


def _contract_has_exception_review_outputs(contract: AuthorOutputContract) -> bool:
    if contract.exception_output_files or contract.exception_rules:
        return True
    required = {column.strip().lower() for column in contract.required_output_columns}
    return bool(required & {"exception_flag", "issue_flag", "review_required"})


def _exception_review_codegen_section(*, reviewed_contract: AuthorOutputContract) -> str:
    lines = [
        "Exception-review rule implementation:",
        "- Implement every exception_rules entry from the contract using explicit "
        "Python conditionals on parsed row fields.",
        "- When amount and policy_limit columns exist, flag rows where amount > policy_limit.",
        "- When receipt columns exist, treat false/no/0/n as missing receipt.",
        "- When approval_status (or manager_approved) exists, flag rows whose approval "
        "value is not approved (case-insensitive), including pending and rejected.",
        "- When notes/description exists, flag rows whose text contains suspicious finance "
        f"keywords such as {', '.join(repr(term) for term in _EXPENSE_SUSPICIOUS_NOTE_TERMS)}.",
        "- Combine multiple hits into one exception_reason string and set review_required "
        "to yes when any rule fires.",
        "- Set exception_flag to yes when any exception rule fires and no otherwise.",
        "- When multiple exception_rules fire on one row, join rule names in rule_used "
        "with '; ' (semicolon space) and join exception_reason strings the same way.",
        "- Write every flagged row from outputs/output.csv into outputs/exceptions.csv; "
        "the exceptions file must contain exactly the rows whose exception_flag is yes "
        "(or review_required=yes when exception_flag is absent).",
        "Exception-review report paths:",
        "- User-facing workflow reports must use workspace-relative paths only "
        "(uploads/input.csv, outputs/output.csv, outputs/exceptions.csv, "
        "reports/validation_report.md).",
        "- Never embed absolute filesystem paths in reports/validation_report.md even when "
        "CLI args are absolute; use contract paths or os.path.relpath against the cwd.",
        "Exception-review confidence semantics:",
        "- severity expresses risk or business impact; confidence expresses certainty "
        "that the rule assignment is correct.",
        "- Do NOT use confidence as a risk score and do NOT lower confidence solely "
        "because severity is high or because multiple deterministic rules fired.",
        "- When rule conditions clearly and deterministically match input fields, "
        "confidence should usually remain high (for example >= 0.85).",
        "- Multiple independent rule hits should generally preserve or increase "
        "confidence, not automatically decrease it.",
        "- Reserve lower confidence for ambiguous inputs, missing fields, parsing "
        "uncertainty, or conflicting signals — not for stacking clear rule hits.",
        "- A row may be high severity and high confidence simultaneously.",
    ]
    if "confidence" in {column.lower() for column in reviewed_contract.required_output_columns}:
        lines.append(
            "- Populate the confidence output column using the semantics above; "
            "never mirror severity into confidence."
        )
    return "\n".join(lines)


def _required_output_column_semantics_section(
    *,
    reviewed_contract: AuthorOutputContract,
) -> str:
    lines = ["Required non-null output columns:"]
    specs = reviewed_contract.required_output_column_semantics()
    for spec in specs:
        lines.append(
            f"- {spec.name}: required={spec.required}; nullable={spec.nullable}; "
            f"allow_empty_string={spec.allow_empty_string}; producer={spec.producer_kind}."
        )
        if spec.description:
            lines.append(f"  Description: {spec.description}")
        lines.append(f"  Row semantics: {spec.row_semantics}")
        lines.append(f"  Fallback semantics: {spec.fallback_value_semantics}")
    lines.append(
        "- Treat missing keys, None, empty strings, and whitespace-only strings as invalid "
        "for every required non-null output column."
    )
    lines.append(
        "- Before writing each output row, ensure every required non-null output column "
        "has a concrete non-empty value."
    )
    return "\n".join(lines)


def _row_completeness_invariant_section(
    *,
    reviewed_contract: AuthorOutputContract,
) -> str:
    if not reviewed_contract.required_output_columns:
        return ""
    columns = ", ".join(reviewed_contract.required_output_columns)
    lines = [
        "Row-completeness invariant (every output row):",
        f"- Every required output column ({columns}) must be populated together on every row.",
        "- No classification branch may set category or confidence without also setting "
        "the other required generated columns on that same row.",
        "- Do not assign None, empty strings, or whitespace-only placeholders to required "
        "non-null output columns.",
        "- Before writing output.csv, run a final per-row normalization pass that fills any "
        "still-empty required generated columns using the selected category/rule/confidence "
        "for that row.",
        "- If no rule matched, use honest fallback labels from the contract semantics rather "
        "than leaving rule_matched or rule_used blank.",
    ]
    return "\n".join(lines)


def _codegen_safety_section() -> str:
    reserved_paths = ", ".join(sorted(_BACKEND_MANAGED_ARTIFACT_PATHS))
    lines = [
        "Static safety requirements:",
        "- Generated Python must pass the backend static safety scan before execution.",
        "- Never use eval(), exec(), compile(), dynamic import, subprocess, os.system, shell calls, or network calls.",
        "- Never parse formulas, filters, conditions, or business rules with eval or another dynamic execution primitive.",
        "- Use explicit Python conditionals, contract-backed deterministic helpers, or the supported safe arithmetic formula DSL only.",
        "- Never access filesystem paths outside the workspace.",
        "- Never write backend orchestration or audit control files. The backend creates these after agent execution.",
        f"- Forbidden write targets include: {reserved_paths}, generated/model_responses/, generated/debug/, and generated/tests/ (except during Author test generation).",
        "- Generated agents may only write CLI-declared outputs (--row-output, --report-path) and contract-declared workflow artifacts under outputs/ and reports/.",
        "- If a requested behavior cannot be implemented safely, raise a clear controlled error instead of using unsafe dynamic execution.",
        "Golden-output separation:",
        "- expected_output.csv, evals/golden_output.csv, and any golden_output_path are validation oracles only.",
        "- The generated agent must NOT read golden or expected-output files to produce its result.",
        "- Golden files may appear only in backend validation after execution, not as codegen implementation guidance.",
    ]
    return "\n".join(lines)


def _contract_runtime_shape_requirements() -> list[str]:
    return [
        "AuthorOutputContract.input_columns is a list of strings. Iterate column names directly.",
        "AuthorOutputContract.output_columns is a list of strings. Iterate column names directly; never use col['name'] for entries from output_columns.",
        "AuthorOutputContract.required_output_columns is a list of strings. Iterate column names directly; never use col['name'] for entries from required_output_columns.",
        "AuthorOutputContract.optional_output_columns, formula_input_columns, and formula_output_columns are also lists of strings.",
        "exception_output_files[].required_columns and summary_output_files[].required_columns are lists of strings; iterate column names directly and never use col['name'].",
        "output_column_semantics, calculated_fields, summary_metrics, summary_output_files, and exception_output_files are lists of objects.",
        "If you unpack multiple variables from a helper call, every return statement in that helper must return the same number of values as the unpack target.",
        "If multiple required row-level output columns come from one helper, return all of them explicitly from every branch. If two fields share the same text, return it twice rather than omitting one.",
        "For the row-level CSV fieldnames, preserve original input columns first, then add contract output_columns that are not already present.",
        "When writing CSVs with csv.DictWriter, fieldnames must cover every key in each row passed to writerow or writerows, or each row must be projected to the declared fieldnames before writing.",
        "Do not pass full output-row dictionaries to a DictWriter configured with a reduced fieldnames list.",
        "For exception CSV outputs, either set fieldnames to the original input columns plus derived exception/review columns, or filter each exception row to the declared fieldnames with {field: row.get(field, '') for field in fieldnames}.",
        "Create every required exception CSV even when there are zero exception rows; still write the header row.",
        "If the report path ends with .md, write readable markdown/text evidence, not a CSV file disguised with a .md extension.",
        "When matching free-text business rules, normalize case and whitespace and match actual keywords in the input fields rather than matching prose labels literally.",
        "When the user or contract says a numeric value must be below a threshold, emit a value strictly below that threshold, not equal to it.",
    ]


def _contract_runtime_shape_section() -> str:
    return "Contract runtime shape requirements:\n" + "\n".join(
        f"- {item}" for item in _contract_runtime_shape_requirements()
    )


def _is_contract_column_list_shape_typeerror(failure_detail: str) -> bool:
    lowered = failure_detail.lower()
    return (
        "typeerror: string indices must be integers" in lowered
        and (
            "contract['output_columns']" in failure_detail
            or 'contract["output_columns"]' in failure_detail
            or "contract['required_output_columns']" in failure_detail
            or 'contract["required_output_columns"]' in failure_detail
            or "col['name']" in failure_detail
            or 'col["name"]' in failure_detail
        )
    )


def _contract_column_list_shape_repair_requirements() -> list[str]:
    return [
        "The runtime failure means generated/agent.py treated a list of strings from the contract as a list of objects.",
        "Replace expressions like [col['name'] for col in contract['output_columns']] with list(contract.get('output_columns') or []).",
        "Replace expressions like set(col['name'] for col in contract['required_output_columns']) with set(contract.get('required_output_columns') or []).",
        "Do not change the contract JSON to work around this; fix generated/agent.py to read the existing strict contract shape.",
        "After the fix, row output fieldnames should preserve input_columns first and append output_columns that are not already included.",
    ]


def _tuple_unpack_target_arity(target: ast.AST) -> int | None:
    if isinstance(target, (ast.Tuple, ast.List)):
        if any(isinstance(element, ast.Starred) for element in target.elts):
            return None
        return len(target.elts)
    return None


def _return_value_arity(value: ast.AST | None) -> int | None:
    if value is None:
        return 0
    if isinstance(value, (ast.Tuple, ast.List)):
        return len(value.elts)
    return None


def _collect_function_return_arities(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[int | None, int]]:
    returns: list[tuple[int | None, int]] = []

    class _ReturnVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
            if node is func:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(  # type: ignore[override]
            self,
            node: ast.AsyncFunctionDef,
        ) -> None:
            if node is func:
                self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
            del node
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:  # type: ignore[override]
            del node
            return

        def visit_Return(self, node: ast.Return) -> None:  # type: ignore[override]
            returns.append((_return_value_arity(node.value), node.lineno))

    _ReturnVisitor().visit(func)
    return returns


def _detect_helper_return_arity_issues(source: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    function_defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name:
            function_defs[node.name] = node

    lines = source.splitlines()
    issues: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        target: ast.AST | None = None
        call: ast.Call | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            call = node.value if isinstance(node.value, ast.Call) else None
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            call = node.value if isinstance(node.value, ast.Call) else None
        if target is None or call is None or not isinstance(call.func, ast.Name):
            continue

        target_arity = _tuple_unpack_target_arity(target)
        helper_name = call.func.id
        helper_node = function_defs.get(helper_name)
        if target_arity is None or helper_node is None:
            continue

        return_arities = _collect_function_return_arities(helper_node)
        if not return_arities or any(arity is None for arity, _line in return_arities):
            continue

        observed = sorted({int(arity) for arity, _line in return_arities if arity is not None})
        if observed == [target_arity]:
            continue

        assignment_line = (
            lines[node.lineno - 1].strip()
            if 1 <= node.lineno <= len(lines)
            else ""
        )
        helper_excerpt = ast.get_source_segment(source, helper_node) or ""
        issues.append(
            {
                "helper_name": helper_name,
                "assignment_target_count": target_arity,
                "observed_return_arities": observed,
                "assignment_line_number": node.lineno,
                "assignment_line": assignment_line,
                "return_line_numbers": [line for _arity, line in return_arities],
                "helper_source_excerpt": helper_excerpt,
            }
        )
    return issues


_TUPLE_UNPACK_VALUEERROR_RE = re.compile(
    r"ValueError:\s+(?:not enough|too many) values to unpack \(expected (?P<expected>\d+), got (?P<got>\d+)\)",
    re.I,
)


def _is_tuple_unpack_arity_valueerror(failure_detail: str) -> bool:
    return _TUPLE_UNPACK_VALUEERROR_RE.search(failure_detail) is not None


def _tuple_unpack_arity_failure_context(
    *,
    failure_detail: str,
    current_files: list[dict[str, str]],
) -> dict[str, Any] | None:
    match = _TUPLE_UNPACK_VALUEERROR_RE.search(failure_detail)
    if match is None:
        return None

    context: dict[str, Any] = {
        "expected_unpack_values": int(match.group("expected")),
        "observed_return_values": int(match.group("got")),
    }
    for item in current_files:
        if item.get("path") != "generated/agent.py":
            continue
        issues = _detect_helper_return_arity_issues(str(item.get("content") or ""))
        if not issues:
            continue
        issue = issues[0]
        context.update(issue)
        break
    return context


def _tuple_unpack_arity_repair_requirements(
    context: dict[str, Any] | None,
) -> list[str]:
    requirements = [
        "The runtime failure means a helper function returned the wrong number of values for an unpack assignment.",
        "Count the variables on the left-hand side and make every return statement in that helper return exactly that many values.",
        "Do not reduce the unpack target or remove contract-required output columns to silence the error.",
        "If rule_matched and rule_used are both required output columns, return both explicitly from every helper branch. If they share the same text, return it twice.",
        "Make every helper branch use a consistent tuple length before writing any output rows.",
    ]
    if not context:
        return requirements
    helper_name = context.get("helper_name")
    expected = context.get("assignment_target_count") or context.get("expected_unpack_values")
    observed = context.get("observed_return_arities") or context.get("observed_return_values")
    if helper_name and expected and observed:
        requirements.insert(
            0,
            f"The failing unpack expects {expected} values from {helper_name} but the helper currently returns {observed}.",
        )
    return requirements


def _literal_string_list(node: ast.AST | None) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        values: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                return None
        return values
    return None


def _is_csv_dictwriter_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "DictWriter":
        return False
    value = func.value
    if isinstance(value, ast.Name) and value.id == "csv":
        return True
    return isinstance(value, ast.Attribute) and value.attr == "csv"


def _detect_dictwriter_fieldname_issues(source: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    writer_fieldnames: dict[str, list[str]] = {}
    issues: list[dict[str, Any]] = []

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_writers: dict[str, list[str]] = {}

        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign) and len(inner.targets) == 1:
                target = inner.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(inner.value, ast.Call) and _is_csv_dictwriter_call(inner.value):
                    fieldnames = None
                    for keyword in inner.value.keywords:
                        if keyword.arg == "fieldnames":
                            fieldnames = _literal_string_list(keyword.value)
                            break
                    if fieldnames is not None:
                        local_writers[target.id] = fieldnames
                        writer_fieldnames[target.id] = fieldnames

            if not isinstance(inner, ast.Expr) or not isinstance(inner.value, ast.Call):
                continue
            call = inner.value
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in {"writerows", "writerow"}:
                continue
            writer_name = call.func.value.id if isinstance(call.func.value, ast.Name) else None
            if writer_name is None or writer_name not in local_writers:
                continue
            rows_arg = call.args[0] if call.args else None
            rows_name = rows_arg.id if isinstance(rows_arg, ast.Name) else None
            fieldnames = local_writers[writer_name]
            issues.append(
                {
                    "writer_variable": writer_name,
                    "fieldnames": fieldnames,
                    "writer_method": call.func.attr,
                    "rows_variable": rows_name,
                    "line_number": inner.lineno,
                    "line": lines[inner.lineno - 1].strip()
                    if 1 <= inner.lineno <= len(lines)
                    else "",
                }
            )

    return issues


_DICTWRITER_FIELDNAMES_VALUEERROR_RE = re.compile(
    r"ValueError:\s+dict contains fields not in fieldnames:\s*(?P<extra>.+)",
    re.I,
)


def _parse_dictwriter_extra_fieldnames(message: str) -> list[str]:
    return re.findall(r"'([^']+)'", message)


def _is_dictwriter_fieldnames_valueerror(failure_detail: str) -> bool:
    return _DICTWRITER_FIELDNAMES_VALUEERROR_RE.search(failure_detail) is not None


def _dictwriter_fieldnames_failure_context(
    *,
    failure_detail: str,
    current_files: list[dict[str, str]],
) -> dict[str, Any] | None:
    match = _DICTWRITER_FIELDNAMES_VALUEERROR_RE.search(failure_detail)
    if match is None:
        return None

    extra_keys = _parse_dictwriter_extra_fieldnames(match.group("extra"))
    context: dict[str, Any] = {
        "extra_row_keys": extra_keys,
        "error_message": match.group(0).strip(),
    }

    location_match = re.search(
        r'File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in [^\n]+)?\n\s*(?P<code>[^\n]+)',
        failure_detail,
    )
    if location_match:
        context["failing_path"] = location_match.group("path")
        context["line_number"] = int(location_match.group("line"))
        context["failing_line"] = location_match.group("code").strip()

    open_match = re.search(
        r"open\(\s*['\"](?P<path>outputs/[^'\"]+\.csv)['\"]",
        failure_detail,
    )
    if open_match:
        context["writer_target_path"] = open_match.group("path")

    for item in current_files:
        if item.get("path") != "generated/agent.py":
            continue
        detected = _detect_dictwriter_fieldname_issues(str(item.get("content") or ""))
        if not detected:
            continue
        issue = detected[-1]
        context.update(
            {
                "writer_variable": issue.get("writer_variable"),
                "configured_fieldnames": issue.get("fieldnames"),
                "rows_variable": issue.get("rows_variable"),
                "writer_method": issue.get("writer_method"),
                "writer_line_number": issue.get("line_number"),
                "writer_line": issue.get("line"),
            }
        )
        configured = set(issue.get("fieldnames") or [])
        if extra_keys and configured:
            context["missing_from_fieldnames"] = [
                key for key in extra_keys if key not in configured
            ]
        break

    return context


def _dictwriter_fieldnames_repair_requirements(
    context: dict[str, Any] | None,
) -> list[str]:
    requirements = [
        "The runtime failure means csv.DictWriter was configured with fieldnames that do not include every key in the rows being written.",
        "Identify the DictWriter fieldnames list and the extra keys present in the row dictionaries.",
        "Fix the writer by either expanding fieldnames to cover every row key you need to preserve, or projecting each row to the declared fieldnames before calling writerow/writerows.",
        "Preferred pattern: safe_row = {field: row.get(field, '') for field in fieldnames}; writer.writerow(safe_row).",
        "Alternative pattern: fieldnames = original_input_columns + derived_output_columns + exception/review columns, with duplicates removed in order.",
        "Do not delete exception_rows or skip writing required exception CSV outputs.",
        "If there are zero exception rows, still create the required exception CSV with its header row.",
        "Preserve required row-level output columns and every required output artifact path from the contract.",
    ]
    if not context:
        return requirements

    configured = context.get("configured_fieldnames")
    extra_keys = context.get("extra_row_keys") or context.get("missing_from_fieldnames")
    target_path = context.get("writer_target_path")
    if configured and extra_keys:
        requirements.insert(
            0,
            "The failing writer uses fieldnames "
            f"{configured} but the rows also contain {extra_keys}.",
        )
    if target_path:
        requirements.insert(
            1 if configured and extra_keys else 0,
            f"The failing CSV writer targets {target_path}.",
        )
    return requirements


def _codegen_execution_interface(*, reviewed_contract: AuthorOutputContract) -> str:
    cli = _generated_agent_cli_contract(reviewed_contract)
    lines = [
        "Required CLI interface:",
        f"- Production command shape: {cli['production_command']}",
        f"- `--input`: {cli['semantics']['--input']}",
        f"- `--contract`: {cli['semantics']['--contract']}",
        f"- `--row-output`: {cli['semantics']['--row-output']}",
        f"- `--report-path`: {cli['semantics']['--report-path']}",
    ]
    for requirement in cli["requirements"]:
        lines.append(f"- {requirement}")
    return "\n".join(lines)


def _codegen_prompt(
    *,
    workflow_type: str,
    user_description: str,
    reviewed_contract: AuthorOutputContract,
    template_hint: str | None = None,
    schema_profile: dict[str, Any] | None = None,
    enable_bundled_bank_reference_guidance: bool = False,
) -> str:
    contract_json = _json_prompt(_compact_codegen_contract(reviewed_contract))
    execution = _codegen_execution_interface(reviewed_contract=reviewed_contract)
    required_artifacts = _required_artifacts_section(reviewed_contract=reviewed_contract)
    report_sections = _report_section_requirements_section(
        reviewed_contract=reviewed_contract
    )
    required_output_semantics = _required_output_column_semantics_section(
        reviewed_contract=reviewed_contract
    )
    row_completeness = _row_completeness_invariant_section(
        reviewed_contract=reviewed_contract
    )
    data_coercion = codegen_data_coercion_section()
    safety_requirements = _codegen_safety_section()
    contract_shape = _contract_runtime_shape_section()
    prompt_sections = [
            "Stage: code_generation",
            f"Workflow type: {workflow_type}",
            "Task: write complete Python source for generated/agent.py only.",
            "Response format: return raw Python source only.",
            "Do not return markdown fences, JSON objects, explanations, or tests.",
            "When matching vendor or payee text, combine transaction description and "
            "counterparty fields when both are available in the input file.",
            execution,
            contract_shape,
            data_coercion,
            safety_requirements,
            required_artifacts,
            report_sections,
            required_output_semantics,
    ]
    if (
        enable_bundled_bank_reference_guidance
        and _is_bundled_bank_reference_contract(
            template_hint=template_hint,
            contract=reviewed_contract,
        )
    ):
        prompt_sections.append(_bundled_bank_reference_codegen_section())
    elif _is_custom_bank_categoriser_contract(
        template_hint=template_hint,
        contract=reviewed_contract,
    ):
        prompt_sections.append(
            _custom_bank_categoriser_codegen_section(
                reviewed_contract=reviewed_contract,
                schema_profile=schema_profile or {},
            )
        )
    elif _contract_has_exception_review_outputs(reviewed_contract):
        prompt_sections.append(_exception_review_codegen_section(reviewed_contract=reviewed_contract))
    if row_completeness:
        prompt_sections.append(row_completeness)
    prompt_sections.extend([
            f"User request: {user_description}",
            "AuthorOutputContract (validated JSON):",
            contract_json,
    ])
    return "\n".join(prompt_sections)


def _codegen_prompt_metrics(
    prompt: str,
    reviewed_contract: AuthorOutputContract,
) -> dict[str, int]:
    contract_json = _json_prompt(_compact_codegen_contract(reviewed_contract))
    return {
        "prompt_chars": len(prompt),
        "prompt_lines": prompt.count("\n") + (1 if prompt else 0),
        "compact_contract_chars": len(contract_json),
    }


def _codegen_max_tokens_for_purpose(purpose: str, settings: Settings | None) -> int | None:
    """Return stage output cap for Ollama; DeepSeek omits caps so responses are not truncated."""
    if settings is not None and settings.llm_provider.strip().lower() == "deepseek":
        return None
    if purpose in ("code_generation", "code_adaptation"):
        return None
    return _STAGE_MAX_TOKENS.get(purpose, 4096)


def _response_near_or_at_output_cap(
    *,
    purpose: str,
    response: Any,
    settings: Settings | None,
) -> bool:
    configured_max_tokens = _codegen_max_tokens_for_purpose(purpose, settings)
    if configured_max_tokens is None:
        return False
    output_tokens = int(getattr(getattr(response, "usage", None), "output_tokens", 0) or 0)
    return output_tokens >= max(1, int(configured_max_tokens * 0.98))


def _parse_failure_detail(
    *,
    stage: str,
    purpose: str,
    response: Any,
    settings: Settings | None,
) -> str:
    configured_max_tokens = _codegen_max_tokens_for_purpose(purpose, settings)
    output_tokens = int(getattr(getattr(response, "usage", None), "output_tokens", 0) or 0)
    parts = [
        f"{stage} did not yield valid JSON",
        f"purpose={purpose}",
        f"configured_max_tokens={configured_max_tokens}",
        f"output_tokens={output_tokens}",
    ]
    if _response_near_or_at_output_cap(
        purpose=purpose,
        response=response,
        settings=settings,
    ):
        parts.append("parse_failure_after_hitting_or_nearing_cap=true")
    return "; ".join(parts)


def _write_codegen_debug_artifacts(
    *,
    workspace: Path,
    system_prompt: str,
    user_prompt: str,
    reviewed_contract: AuthorOutputContract,
    model_client: ModelClient,
    settings: Settings | None,
    max_tokens: int | None,
    timeout_seconds: float,
    client_override: ModelClient | None = None,
) -> dict[str, Any]:
    """Persist codegen prompt diagnostics under ``generated/debug/``."""
    debug_dir = workspace / "generated" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "codegen_prompt.txt").write_text(user_prompt, encoding="utf-8")

    client = client_override or _client_for_purpose(
        model_client=model_client,
        purpose="code_generation",
        settings=settings,
    )
    metadata = getattr(client, "metadata", {}) or {}
    prompt_metrics = _codegen_prompt_metrics(user_prompt, reviewed_contract)
    required_output_paths = reviewed_contract.all_required_output_paths()
    options_sent: dict[str, Any] = {}
    if isinstance(client, OllamaModelClient):
        options_sent = client.request_options(max_tokens=max_tokens)
    meta: dict[str, Any] = {
        **prompt_metrics,
        "required_output_files": len(required_output_paths),
        "required_output_file_paths": required_output_paths,
        "output_format": "raw_python",
        "output_format_requested": "raw_python",
        "system_prompt_chars": len(system_prompt),
        "provider": metadata.get("provider", "unknown"),
        "model": metadata.get("model", "unknown"),
        "temperature": getattr(client, "_temperature", None),
        "num_ctx": getattr(client, "_num_ctx", None),
        "stream": False,
        "options_sent_to_ollama": options_sent,
        "num_predict_set": "num_predict" in options_sent,
        "num_predict": options_sent.get("num_predict"),
        "max_tokens_set": max_tokens is not None,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (debug_dir / "codegen_prompt_meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return meta


def _strip_markdown_python_fence(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:python)?\s*([\s\S]*?)```", stripped, re.I)
    if fence:
        return fence.group(1).strip()
    return stripped


def _parse_codegen_agent_source(raw_text: str) -> tuple[str | None, str | None]:
    """Parse raw Python or legacy JSON files-list codegen output."""
    if not raw_text or not raw_text.strip():
        return None, "empty response"
    text = _strip_markdown_python_fence(raw_text)
    payload = _extract_json_payload(text)
    if isinstance(payload, dict):
        files = _normalise_files_payload(payload)
        if files and _REQUIRED_AGENT_PATH in files:
            content = files[_REQUIRED_AGENT_PATH]
            if content.strip():
                return content if content.endswith("\n") else content + "\n", None
    file_envelope = re.search(
        r"^#\s*FILE:\s*generated/agent\.py\s*\n([\s\S]+)$",
        text,
        re.MULTILINE,
    )
    if file_envelope:
        body = file_envelope.group(1).strip()
        if body and any(body.startswith(marker) for marker in _AGENT_SOURCE_MARKERS):
            return body + ("\n" if not body.endswith("\n") else ""), None
    if any(text.lstrip().startswith(marker) for marker in _AGENT_SOURCE_MARKERS):
        return text + ("\n" if not text.endswith("\n") else ""), None
    return None, "missing Python source markers for generated/agent.py"


def _codegen_failure_detail(*, failure: str, error: str | None = None) -> str:
    parts = ["purpose=code_generation", f"failure={failure}"]
    if error:
        parts.append(f"error={error}")
    return "; ".join(parts)


def _generated_test_workspace_path_requirements() -> list[str]:
    return [
        "Pytest runs with cwd pinned to the session workspace root. All artifact paths are relative to that root.",
        "Resolve the workspace root from generated/tests/test_agent.py using WORKSPACE_ROOT = Path(__file__).resolve().parents[2].",
        "Invoke generated/agent.py at WORKSPACE_ROOT / 'generated/agent.py'. Read the contract from WORKSPACE_ROOT / 'generated/author_output_contract.json'. Use uploads under WORKSPACE_ROOT / 'uploads/' and write outputs under WORKSPACE_ROOT / 'outputs/' and reports under WORKSPACE_ROOT / 'reports/'.",
        "Use workspace-relative CLI paths (for example uploads/input.csv, generated/author_output_contract.json, outputs/output.csv, reports/validation_report.md) with cwd=WORKSPACE_ROOT when using subprocess.",
        "Never pass absolute paths to --input, --row-output, or --report-path; absolute CLI paths leak into user-facing validation_report.md.",
        "Behavioural tests that use alternate input fixtures must not overwrite production "
        "outputs/output.csv; use separate output paths or restore production outputs by "
        "re-running the agent against uploads/input.csv before the test completes.",
        "Do not use pytest tmp_path, tmp_path_factory, or monkeypatch.chdir to build an isolated workspace unless you also copy generated/agent.py and the production AuthorOutputContract into that directory.",
        "Prefer calling the production CLI against the real workspace artifacts instead of fabricating a parallel contract/input tree under tmp_path.",
        "If you need importlib-based testing, load generated/agent.py from WORKSPACE_ROOT / 'generated/agent.py' via spec_from_file_location and pass explicit argv paths rooted at WORKSPACE_ROOT.",
    ]


def _expense_exception_test_generation_requirements(
    *,
    reviewed_contract: AuthorOutputContract,
    schema_profile: dict[str, Any],
) -> list[str]:
    if not _is_expense_exception_schema(schema_profile):
        return []
    if not _contract_has_exception_review_outputs(reviewed_contract):
        return []
    rule_names = [
        str(rule.name).strip()
        for rule in reviewed_contract.exception_rules or []
        if str(getattr(rule, "name", "") or "").strip()
    ]
    allowed_enum_columns = sorted(
        (reviewed_contract.allowed_enums or {}).keys()
    )
    requirements = [
        "When the contract includes expense exception rules, add at least one "
        "workflow-specific behavioural test that exercises an obvious exception case "
        "traceable to exception_rules (for example amount over policy_limit, missing "
        "receipt, non-approved status, or suspicious notes).",
        "Build synthetic mini-fixtures inside the test from contract rule conditions; "
        "do not hardcode uploaded sample expense_id values or exact sample CSV dollar amounts.",
        "Synthetic behavioural tests must use alternate row-output paths (for example "
        "outputs/synth_output.csv) and must NOT write to production outputs/output.csv; "
        "the generated agent always writes the exception CSV to the contract "
        "exception_output_files path (typically outputs/exceptions.csv), so read that "
        "contract path after synthetic runs instead of inventing outputs/synth_exceptions.csv "
        "sibling paths that the CLI does not create.",
        "After synthetic behavioural tests, restore production outputs/output.csv and "
        "outputs/exceptions.csv by re-running the agent against uploads/input.csv before "
        "the test finishes; never leave fixture output as the final production CSV.",
        "Use exception_flag/review_required values yes/no from contract allowed_enums; "
        "never assert no_issue, review_required as a flag value, or other invented enums.",
        "Assert exception_flag/review_required semantics from the contract rather than "
        "inventing stricter finance policy than exception_rules declares.",
        "Prefer stable structured field assertions on production output rows: expense_id, "
        "exception_flag, review_required, severity, and rule_used. Do not require exact "
        "exception_reason prose on production upload rows unless the contract explicitly "
        "requires exact literal text for that column.",
        "On production outputs/output.csv, verify exception_reason is non-empty for required "
        "rows rather than matching a hardcoded sentence copied from exception_rules.reason.",
        "Apply allowed_enums checks only to columns listed under contract allowed_enums "
        f"(for example {allowed_enum_columns or ['exception_flag', 'review_required', 'severity']}); "
        "do not invent a separate hardcoded rule_used enum set when rule_used is absent from "
        "allowed_enums.",
        "When validating rule_used, derive permitted names from contract exception_rules "
        "names plus 'none' by reading the contract JSON; do not hardcode rule names in the "
        "test source.",
        "When rule_used may list multiple exception_rules on one row, split on ';' (not ',') "
        "and validate each semicolon-separated token against permitted rule names "
        "(containment) rather than requiring exact whole-string equality against a single "
        "rule id or treating the undelimited string as one token.",
        "Do not recompute expected exception fields for every production upload row inside "
        "compute_expected_exception-style helpers with exact reason prose; reserve full "
        "rule-condition behavioural checks for synthetic mini-fixtures built inside the test.",
        "When asserting review_required and exception_flag together, verify they are "
        "consistent (review_required=yes iff exception_flag equals the contract flagged "
        "enum value) rather than comparing every derived field against local reimplementation "
        "logic on the canonical upload sample.",
        "When synthetic behavioural tests read exception_output_files or summary_output_files "
        "from CONTRACT JSON, treat required_columns as a list of strings (not objects with "
        "name fields).",
    ]
    if rule_names:
        requirements.append(
            "Contract exception_rules names for rule_used validation: "
            f"{', '.join(rule_names)} plus 'none'."
        )
    return requirements


def _is_expense_exception_pytest_brittle_failure(failure_detail: str) -> bool:
    lowered = failure_detail.lower()
    expense_test_markers = (
        "test_exception_rules_logic",
        "test_allowed_enums",
        "test_synthetic",
        "test_rule_used_permitted_names",
        "required_columns",
    )
    if not any(marker in lowered for marker in expense_test_markers):
        return False
    brittle_markers = (
        "field 'exception_reason' mismatch",
        "column 'rule_used' has value",
        "unexpected rule_used '",
        "which is not in allowed set",
        "not in permitted names",
        "unexpected rule token",
        "field 'exception_flag' mismatch",
        "string indices must be integers, not 'str'",
        "col['name']",
        "synth_exceptions",
        "no such file or directory",
        "filenotfounderror",
    )
    if any(marker in lowered for marker in brittle_markers):
        return True
    if "test_rule_used_permitted_names" in lowered and ";" in failure_detail:
        return True
    return False


def _expense_exception_pytest_repair_requirements(
    *,
    contract: AuthorOutputContract,
) -> list[str]:
    rule_names = [
        str(rule.name).strip()
        for rule in contract.exception_rules or []
        if str(getattr(rule, "name", "") or "").strip()
    ]
    allowed_enum_columns = sorted((contract.allowed_enums or {}).keys())
    requirements = [
        "This is an expense exception review pytest repair. Align tests with the "
        "AuthorOutputContract and current generated outputs; do not rewrite generated/agent.py "
        "unless required-column null counts prove an agent violation.",
        "Remove or relax exact exception_reason string equality checks on production upload "
        "rows; require non-empty exception_reason for required rows instead.",
        "Prefer stable field checks on production output: exception_flag, review_required, "
        "severity, rule_used, and expense_id traceability.",
        "Apply allowed_enums only to columns listed in contract allowed_enums "
        f"({allowed_enum_columns or ['exception_flag', 'review_required', 'severity']}); "
        "do not hardcode a rule_used allowed set when rule_used is not an allowed_enums key.",
        "Derive permitted rule_used values from contract exception_rules names plus 'none'.",
        "When rule_used holds multiple rules joined by '; ', split on ';' (not ',') and assert "
        "each token is permitted rather than requiring exact whole-string equality.",
        "Synthetic behavioural tests must read the exception CSV from the contract "
        "exception_output_files path (typically outputs/exceptions.csv); the agent does not "
        "write alternate synth_exceptions paths.",
        "After synthetic tests, restore production outputs/output.csv and outputs/exceptions.csv "
        "by re-running the agent on uploads/input.csv.",
        "Use exception_flag/review_required yes/no only; do not assert no_issue or "
        "review_required as an exception_flag value.",
        "Do not invent suspicious_keywords or other rule names in tests unless they appear in "
        "contract exception_rules.",
        "Reserve full exception_rules behavioural checks for synthetic mini-fixtures; do not "
        "compare every production row against a local compute_expected helper with hardcoded "
        "reason prose.",
        "When reading exception_output_files from CONTRACT JSON, required_columns are strings; "
        "never use col['name'] on required_columns entries.",
        "Preserve passing artifact, row-count, non-null, and exceptions-file tests.",
    ]
    if rule_names:
        requirements.append(
            "Permitted rule_used names from contract: "
            f"{', '.join(rule_names)} and none."
        )
    return requirements


_PYTEST_WORKSPACE_PATHING_FAILURE_RE = re.compile(
    r"can't open file '(?P<path>[^']*pytest[^']*/generated/agent\.py)'.*\[Errno 2\] No such file",
    re.IGNORECASE,
)


def _is_pytest_workspace_pathing_failure(failure_detail: str) -> bool:
    if _PYTEST_WORKSPACE_PATHING_FAILURE_RE.search(failure_detail):
        return True
    lowered = failure_detail.lower()
    if "generated/agent.py" not in lowered:
        return False
    if "no such file" not in lowered and "filenotfounderror" not in lowered:
        return False
    return "pytest-of" in lowered or "tmp_path" in lowered or "/tmp/" in lowered or "/private/tmp/" in lowered


def _pytest_workspace_pathing_failure_context(
    *,
    failure_detail: str,
    current_files: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not _is_pytest_workspace_pathing_failure(failure_detail):
        return None

    context: dict[str, Any] = {
        "failure_kind": "pytest_workspace_pathing",
        "error_message": "Generated tests invoked generated/agent.py from a pytest temporary directory where the agent file was absent.",
    }
    match = _PYTEST_WORKSPACE_PATHING_FAILURE_RE.search(failure_detail)
    if match:
        context["missing_agent_path"] = match.group("path")
    for item in current_files:
        if item.get("path") != "generated/tests/test_agent.py":
            continue
        content = str(item.get("content") or "")
        if "tmp_path" in content:
            context["uses_tmp_path_fixture"] = True
        if "tmp_path / \"generated\" / \"agent.py\"" in content or (
            "tmp_path / 'generated' / 'agent.py'" in content
        ):
            context["invokes_agent_under_tmp_path"] = True
        break
    return context


def _pytest_workspace_pathing_repair_requirements(
    context: dict[str, Any] | None,
) -> list[str]:
    requirements = [
        "The pytest failure means generated/tests/test_agent.py tried to run generated/agent.py from a temporary directory instead of the session workspace root.",
        "Resolve WORKSPACE_ROOT = Path(__file__).resolve().parents[2] from generated/tests/test_agent.py.",
        "Invoke WORKSPACE_ROOT / 'generated/agent.py' with workspace-relative CLI paths and cwd=WORKSPACE_ROOT (or omit cwd so pytest's workspace-root cwd is used).",
        "Read generated/author_output_contract.json from the workspace root; do not invent a parallel contract under tmp_path unless you also copy generated/agent.py there.",
        "Use uploads/input.csv (or the contract's input_file) and write outputs under outputs/ and reports/ relative to WORKSPACE_ROOT.",
        "Remove tmp_path-based agent invocation unless you copy generated/agent.py and the production contract into that temp directory.",
        "If the failure mentions FileNotFoundError, ImportError, or 'can't open file ... generated/agent.py', fix path resolution before changing business assertions.",
    ]
    if context and context.get("invokes_agent_under_tmp_path"):
        requirements.insert(
            0,
            "The current test file calls tmp_path / 'generated/agent.py'; replace that with WORKSPACE_ROOT / 'generated/agent.py'.",
        )
    return requirements


def _test_generation_prompt(
    *,
    workflow_type: str,
    user_description: str,
    reviewed_contract: AuthorOutputContract,
    template_hint: str | None = None,
    schema_profile: dict[str, Any] | None = None,
    enable_bundled_bank_reference_guidance: bool = False,
) -> str:
    test_quality_requirements = [
        *_generated_test_workspace_path_requirements(),
        "Generated tests should include three layers where applicable:",
        "1. Universal artifact/runtime checks (required files exist, agent CLI succeeds, row count preserved when contract requests it).",
        "2. Contract-specific checks from AuthorOutputContract (required_output_columns, allowed_enums, exception files, summary files, requested_deliverables).",
        "3. Workflow-specific behavioural tests when supported by the user prompt, uploaded sample rows, and AuthorOutputContract.",
        "Workflow-specific behavioural tests are allowed and desirable when traceable to prompt/contract/sample.",
        "Generated tests must NOT:",
        "- invent business rules absent from prompt/contract/sample",
        "- require exact report wording unless the contract explicitly requires exact text",
        "- require every enum label to appear unless sample/contract requires it",
        "- assert unsupported confidence or severity assumptions not declared in the contract",
        "- use expected_output.csv or golden_output_path as a codegen or implementation shortcut",
        "- skip required artifact checks",
        "- rely on nonexistent contract fields such as required_artifacts inside author_output_contract.json",
        "Define at least three top-level pytest-discoverable functions named test_*.",
        "Every generated test must be a top-level function whose name starts with test_ so pytest collects it.",
        "Use string-typed numeric input fixtures where CSV/XLSX readers may return strings (for example invoice_amount='1000.50').",
        "Derive assertions from the AuthorOutputContract and the user request. Do not invent stricter requirements than the contract states.",
        "The pytest file must be fully self-contained. Import every module and symbol it uses explicitly. If you use csv.DictReader import csv, if you use subprocess.run import subprocess, if you use sys.executable import sys, and if you use Path import pathlib.Path. Before returning, verify that every referenced module has a matching import.",
        "Parse row-level CSV outputs with csv.DictReader or another header-aware parser. Never treat the header row as data.",
        "For markdown or summary outputs, assert contract-backed sections or semantic requirements rather than an invented literal phrase unless the contract explicitly requires exact text.",
        "Summary metric names such as category_counts or uncertain_rows_count are internal identifiers unless the contract explicitly marks exact wording as required. Do not assert raw snake_case metric names as literal report text by default.",
        "When the contract requires report coverage for summary metrics or report required_columns, prefer semantic checks with normalized human-readable labels (for example category_counts -> Category Counts, uncertain_rows_count -> Uncertain Rows Count) or compute the metric from outputs/output.csv and verify matching evidence in the report when feasible.",
        "If a business-rule check comes from the contract formula, assert that formula against parsed output rows rather than against raw CSV line strings.",
        "If the test reads the AuthorOutputContract, remember that input_columns, output_columns, and required_output_columns are lists of strings, not objects with name fields.",
        "Only turn validation_checks with required=true into failing pytest assertions. Do not turn non-required, advisory, reporting, or summary requirements into hard pytest failures.",
        "Assert only required artifacts listed in required_artifacts unless the contract explicitly marks an additional artifact as required.",
        "Use real contract artifact fields from this prompt: row_level_output_file, summary_output_files, exception_output_files, and requested_deliverables — not only a generic required_artifacts alias.",
        "Embed REQUIRED_ARTIFACTS as a module-level list derived from the model-authored contract fields surfaced in this prompt's required_artifacts (row_level_output_file, summary_output_files, exception_output_files, requested_deliverables).",
        "Add a short header comment above REQUIRED_ARTIFACTS: Required artifacts derived from the model-authored contract and embedded by the test generator.",
        "Do NOT read required output paths only from CONTRACT.get('required_artifacts') — that alias is prompt-side only and is not persisted in author_output_contract.json.",
        "Assert that every path listed in REQUIRED_ARTIFACTS exists after the agent run and can be opened and read successfully.",
        "When a required exception_csv artifact has zero data rows, assert that the file still exists and that its header row contains the contract-required columns.",
        "When a required summary_csv artifact is listed, assert that the file exists and that its header row contains the contract-required grouping and metric columns.",
        "Do not treat the row-level output alone as sufficient when required_artifacts includes additional summary or exception files.",
        "Assert that every required non-null output column contains a non-empty value for every data row.",
        "Treat missing values, None, empty strings, and whitespace-only strings as invalid for required non-null output columns.",
        "Every assertion must be traceable to the reviewed contract, the explicit CLI contract, the user request, or the required artifact list.",
        "Do not invent domain heuristics or implicit finance assumptions. Do not assert amount sign by category unless the contract explicitly requires that rule.",
        "Add one short comment above each test naming the contract requirement or artifact it verifies.",
    ]
    classification_requirements = _classification_test_generation_requirements(
        reviewed_contract
    )
    if classification_requirements:
        test_quality_requirements.extend(classification_requirements)
    if enable_bundled_bank_reference_guidance and _is_bundled_bank_reference_contract(
        template_hint=template_hint,
        contract=reviewed_contract,
    ):
        test_quality_requirements.extend(
            _bundled_bank_reference_test_generation_requirements()
        )
    elif _is_custom_bank_categoriser_contract(
        template_hint=template_hint,
        contract=reviewed_contract,
    ):
        test_quality_requirements.extend(
            _custom_bank_categoriser_test_generation_requirements(
                reviewed_contract=reviewed_contract,
                schema_profile=schema_profile or {},
            )
        )
    else:
        expense_test_requirements = _expense_exception_test_generation_requirements(
            reviewed_contract=reviewed_contract,
            schema_profile=schema_profile or {},
        )
        if expense_test_requirements:
            test_quality_requirements.extend(expense_test_requirements)
    return _json_prompt(
        {
            "stage": "test_generation",
            "workflow_type": workflow_type,
            "user_description": user_description,
            "author_output_contract": _compact_codegen_contract(reviewed_contract),
            "required_artifacts": _required_artifact_specs(reviewed_contract),
            "optional_artifact_paths": _optional_artifact_paths(reviewed_contract),
            "required_output_column_semantics": [
                spec.model_dump(mode="json")
                for spec in reviewed_contract.required_output_column_semantics()
            ],
            "agent_files": ["generated/agent.py"],
            "required_cli_interface": _generated_agent_cli_contract(reviewed_contract),
            "workspace_execution_context": {
                "pytest_cwd": "session workspace root",
                "workspace_root_resolution": (
                    "WORKSPACE_ROOT = Path(__file__).resolve().parents[2] "
                    "from generated/tests/test_agent.py"
                ),
                "agent_path": "generated/agent.py",
                "contract_path": "generated/author_output_contract.json",
                "uploads_dir": "uploads/",
                "outputs_dir": "outputs/",
                "reports_dir": "reports/",
            },
            "data_coercion_guidance": codegen_data_coercion_guidance(),
            "contract_runtime_shape_requirements": _contract_runtime_shape_requirements(),
            "test_quality_requirements": test_quality_requirements,
            "required_response_shape": {
                "files": [
                    {
                        "path": "generated/tests/test_agent.py",
                        "content": "complete pytest source",
                    }
                ],
                "notes": "short string",
                "assumptions": [],
            },
            "instruction": (
                "Return strict JSON only with `files` as a list. Include "
                "generated/tests/test_agent.py only. The tests must independently "
                "invoke generated/agent.py using the exact production CLI "
                "(python generated/agent.py --input ... --contract ... --row-output "
                "outputs/output.csv --report-path reports/validation_report.md or "
                "the contract's required paths). Define at least three test_* "
                "functions. The tests must independently "
                "check the generated agent against the AuthorOutputContract: required "
                "columns, required non-null columns, row count preservation when requested, and required artifacts "
                "listed in required_artifacts. Embed REQUIRED_ARTIFACTS from the prompt's required_artifacts list; "
                "do not read required_artifacts from author_output_contract.json because that key is not persisted. "
                "Assert that every required artifact exists and is readable, and when a required exception or summary CSV has zero data rows still verify that the header row exists. Use contract-backed assertions only. "
                "If the contract includes an explicit machine-checkable business rule "
                "with required=true (for example an allowed enum, formula, or exception/filter rule that "
                "applies to output rows), you may assert one of those; otherwise do "
                "not invent extra domain tests. The tests must invoke generated/agent.py using the same "
                "CLI argument shape as production execution, either via subprocess or "
                "by calling a main(argv) entrypoint with the same flags. Resolve "
                "paths from the session workspace root (Path(__file__).resolve().parents[2] "
                "from generated/tests/test_agent.py); do not invoke generated/agent.py "
                "from pytest tmp_path unless the agent and contract are copied there. When the "
                "contract describes report semantics, assert the required sections or "
                "evidence rather than copying the deliverable description as a literal "
                "expected sentence. Treat summary metric identifiers and report "
                "required_columns as internal contract labels unless exact text is "
                "explicitly required; prefer normalized human-readable headings or "
                "data-derived evidence checks instead of raw snake_case string "
                "matches. Each test must start with a short contract trace "
                "comment that names the requirement it verifies. Tests must not call network commands."
            ),
        }
    )


def _record_model_call_started(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    purpose: str,
    attempt: int | None = None,
) -> None:
    metadata = getattr(model_client, "metadata", {}) or {}
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "model_call_started",
            "stage": stage_label,
            "purpose": purpose,
            "provider": metadata.get("provider", "unknown"),
            "model": metadata.get("model", "unknown"),
            **({"attempt": attempt} if attempt is not None else {}),
        },
        step=step,
    )


def _client_for_purpose(
    *,
    model_client: ModelClient,
    purpose: str,
    settings: Settings | None,
) -> ModelClient:
    provider = _llm_provider_name(settings)
    if provider == "ollama":
        if not isinstance(model_client, OllamaModelClient):
            return model_client
        if purpose in _PLANNING_PURPOSES:
            model = settings.ollama_planning_model if settings else model_client.model
            num_ctx = settings.ollama_planning_num_ctx if settings else model_client._num_ctx
            temperature = None
        else:
            model = settings.ollama_codegen_model if settings else model_client.model
            num_ctx = settings.ollama_num_ctx if settings else model_client._num_ctx
            temperature = settings.ollama_codegen_temperature if settings else None
        base_url = model_client.base_url
        timeout = (
            _timeout_seconds_for_purpose(purpose, settings)
            if settings is not None
            else model_client._timeout
        )
        return OllamaModelClient(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout,
            num_ctx=num_ctx,
            temperature=temperature,
        )

    if provider == "deepseek":
        if settings is None or not isinstance(model_client, DeepSeekModelClient):
            return model_client
        model = _deepseek_model_for_purpose(purpose=purpose, settings=settings)
        if purpose in {
            "contract_planning",
            "contract_review",
            "contract_revision",
            "contract_schema_repair",
        }:
            temperature = None
        else:
            temperature = settings.deepseek_temperature
        return DeepSeekModelClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=model,
            timeout_seconds=_timeout_seconds_for_purpose(purpose, settings),
            temperature=temperature,
            max_retries=settings.deepseek_max_retries,
        )

    return model_client


def _deepseek_model_for_purpose(*, purpose: str, settings: Settings) -> str:
    stage_models = deepseek_author_stage_models(settings)
    if purpose == "contract_planning":
        return stage_models["planning"]
    if purpose in {"contract_review", "contract_revision", "contract_schema_repair"}:
        return stage_models["review"]
    if purpose in _CODEGEN_PURPOSES:
        return stage_models["codegen"]
    if purpose in _TESTGEN_PURPOSES:
        return stage_models["testgen"]
    return stage_models["repair"]


def _bundled_bank_reference_demo_client_override(
    *,
    model_client: ModelClient,
    settings: Settings | None,
    purpose: str,
    enabled: bool,
) -> ModelClient | None:
    if (
        not enabled
        or settings is None
        or settings.llm_provider.strip().lower() != "ollama"
        or not isinstance(model_client, OllamaModelClient)
    ):
        return None
    if purpose not in {"code_generation", "test_generation", "execution_repair"}:
        return None
    return OllamaModelClient(
        base_url=model_client.base_url,
        model=settings.ollama_planning_model,
        timeout_seconds=_timeout_seconds_for_purpose(purpose, settings),
        num_ctx=settings.ollama_planning_num_ctx,
        temperature=None,
    )


def _deepseek_strong_client(
    *,
    model_client: ModelClient,
    settings: Settings,
    purpose: str,
) -> DeepSeekModelClient:
    temperature = (
        None if purpose in _PLANNING_PURPOSES else settings.deepseek_temperature
    )
    return DeepSeekModelClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=_deepseek_model_for_purpose(purpose=purpose, settings=settings),
        timeout_seconds=_timeout_seconds_for_purpose(purpose, settings),
        temperature=temperature,
        max_retries=settings.deepseek_max_retries,
    )


def _repair_client_for_attempt(
    *,
    model_client: ModelClient,
    settings: Settings | None,
    attempt: int,
    failure_kind: str | None = None,
    prefer_planning_model: bool = False,
) -> ModelClient:
    provider = _llm_provider_name(settings)
    if prefer_planning_model:
        override = _bundled_bank_reference_demo_client_override(
            model_client=model_client,
            settings=settings,
            purpose="execution_repair",
            enabled=True,
        )
        if override is not None:
            return override
    if (
        failure_kind == "pytest"
        and settings is not None
        and provider == "ollama"
        and isinstance(model_client, OllamaModelClient)
    ):
        return OllamaModelClient(
            base_url=model_client.base_url,
            model=settings.ollama_planning_model,
            timeout_seconds=_timeout_seconds_for_purpose("execution_repair", settings),
            num_ctx=settings.ollama_planning_num_ctx,
            temperature=None,
        )
    if (
        failure_kind == "pytest"
        and settings is not None
        and provider == "deepseek"
        and isinstance(model_client, DeepSeekModelClient)
    ):
        return _deepseek_strong_client(
            model_client=model_client,
            settings=settings,
            purpose="execution_repair",
        )
    repair_client = _client_for_purpose(
        model_client=model_client,
        purpose="execution_repair",
        settings=settings,
    )
    if (
        attempt <= 1
        or settings is None
        or provider not in {"ollama", "deepseek"}
    ):
        return repair_client
    if provider == "ollama" and isinstance(model_client, OllamaModelClient):
        return OllamaModelClient(
            base_url=model_client.base_url,
            model=settings.ollama_planning_model,
            timeout_seconds=_timeout_seconds_for_purpose("execution_repair", settings),
            num_ctx=settings.ollama_planning_num_ctx,
            temperature=None,
        )
    if provider == "deepseek" and isinstance(model_client, DeepSeekModelClient):
        return _deepseek_strong_client(
            model_client=model_client,
            settings=settings,
            purpose="execution_repair",
        )
    return repair_client


def _record_model_call_progress(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    purpose: str,
    elapsed_seconds: int,
    attempt: int | None = None,
) -> None:
    metadata = getattr(model_client, "metadata", {}) or {}
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "model_call_progress",
            "stage": stage_label,
            "purpose": purpose,
            "elapsed_seconds": elapsed_seconds,
            "provider": metadata.get("provider", "unknown"),
            "model": metadata.get("model", "unknown"),
            **({"attempt": attempt} if attempt is not None else {}),
        },
        step=step,
    )


async def _call_model(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    system_prompt: str,
    user_prompt: str,
    purpose: str,
    settings: Settings | None = None,
    client_override: ModelClient | None = None,
    attempt: int | None = None,
) -> tuple[Any | None, str | None, int]:
    """Await the configured model client without blocking the FastAPI event loop."""
    client = client_override or _client_for_purpose(
        model_client=model_client,
        purpose=purpose,
        settings=settings,
    )
    max_tokens = _codegen_max_tokens_for_purpose(purpose, settings)
    phase = "author.build"
    record_model_orchestrated_call(
        event_log=event_log,
        session_id=session_id,
        step=step,
        purpose=purpose,
        phase=phase,
        status="started",
    )
    _record_model_call_started(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=client,
        purpose=purpose,
        attempt=attempt,
    )
    started = asyncio.get_running_loop().time()

    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                elapsed = max(0, int(asyncio.get_running_loop().time() - started))
                _record_model_call_progress(
                    session_id=session_id,
                    event_log=event_log,
                    step=step,
                    stage_label=stage_label,
                    model_client=client,
                    purpose=purpose,
                    elapsed_seconds=elapsed,
                    attempt=attempt,
                )
        except asyncio.CancelledError:
            raise

    heartbeat = asyncio.create_task(_heartbeat(), name=f"model-heartbeat-{purpose}")
    try:
        response = await client.complete(
            system_prompt=system_prompt,
            messages=[ModelMessage(role="user", content=[TextBlock(text=user_prompt)])],
            tools=[],
            max_tokens=max_tokens,
        )
        elapsed_ms = max(0, int((asyncio.get_running_loop().time() - started) * 1000))
        record_model_orchestrated_call(
            event_log=event_log,
            session_id=session_id,
            step=step,
            purpose=purpose,
            phase=phase,
            status="completed",
        )
        return response, None, elapsed_ms
    except ModelClientError as exc:
        elapsed = max(0, int(asyncio.get_running_loop().time() - started))
        detail = _model_call_failure_detail(
            purpose=purpose,
            exc=exc,
            elapsed_seconds=elapsed,
            timeout_seconds=_configured_timeout_seconds(client, settings, purpose=purpose),
            client=client,
        )
        _logger.warning("model authoring call failed (%s): %s", purpose, detail)
        record_model_orchestrated_call(
            event_log=event_log,
            session_id=session_id,
            step=step,
            purpose=purpose,
            phase=phase,
            status="failed",
            detail=detail,
        )
        return None, detail, elapsed * 1000
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_authoring_summary(
    *,
    workspace: Path,
    metadata: dict[str, Any],
    model: str | None,
    provider: str | None,
    stages: list[str],
    file_provenance: list[dict[str, Any]],
) -> str:
    rel = "reports/model_authoring_summary.md"
    lines = [
        "# Model authoring summary",
        "",
        f"- Provider: {provider or 'unknown'}",
        f"- Model: {model or 'unknown'}",
        f"- Model stages: {', '.join(stages) if stages else '(none)'}",
        f"- Model calls: {metadata.get('model_call_count', 0)}",
        f"- Token count: {metadata.get('tokens_used', 0)}",
        f"- Validation result: {metadata.get('validation_result', 'pending')}",
        "",
        "## Generated files",
        "",
    ]
    model_by_stage = metadata.get("model_by_stage")
    if isinstance(model_by_stage, dict) and model_by_stage:
        lines.extend(["## Model routing", ""])
        for stage in stages:
            stage_model = model_by_stage.get(stage)
            if isinstance(stage_model, str) and stage_model:
                lines.append(f"- `{stage}`: `{stage_model}`")
        lines.append("")
    for entry in file_provenance:
        lines.extend(
            [
                f"### `{entry['path']}`",
                f"- Contribution: {entry['contribution']}",
                f"- Model output hash: `{entry.get('model_output_hash', '')}`",
                f"- Final file hash: `{entry.get('final_hash', '')}`",
            ]
        )
        if entry.get("reference_hash"):
            lines.append(f"- Reference scaffold hash: `{entry['reference_hash']}`")
        lines.append("")
    output_artifacts = metadata.get("output_artifacts") or []
    if output_artifacts:
        lines.extend(["## Output artifacts", ""])
        for artifact in output_artifacts:
            lines.append(f"- `{artifact}`")
        lines.append("")
    repair_candidates = metadata.get("repair_candidates")
    if isinstance(repair_candidates, list) and repair_candidates:
        lines.extend(["## Repair candidates", ""])
        for candidate in repair_candidates:
            if not isinstance(candidate, dict):
                continue
            attempt = candidate.get("attempt", "?")
            status = candidate.get("status", "unknown")
            model_name = candidate.get("model", "unknown")
            failure_kind = candidate.get("failure_kind")
            paths = candidate.get("candidate_paths") or []
            line = f"- Attempt {attempt}: {status}; model=`{model_name}`"
            if isinstance(failure_kind, str) and failure_kind:
                line += f"; failure_kind=`{failure_kind}`"
            if isinstance(paths, list) and paths:
                line += f"; candidates={', '.join(f'`{path}`' for path in paths)}"
            lines.append(line)
            syntax_error = candidate.get("syntax_error")
            if isinstance(syntax_error, str) and syntax_error:
                lines.append(f"  - Syntax detail: {syntax_error.splitlines()[0]}")
        lines.append("")
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return rel


def _write_response_artifacts(
    *,
    responses_dir: Path,
    stage: str,
    prompt: str,
    response_text: str,
) -> None:
    (responses_dir / f"{stage}.prompt.txt").write_text(prompt, encoding="utf-8")
    (responses_dir / f"{stage}.txt").write_text(response_text, encoding="utf-8")


def _nearby_code_excerpt(*, lines: list[str], line_number: int | None) -> str:
    if line_number is None or not lines:
        return ""
    start = max(1, line_number - 2)
    end = min(len(lines), line_number + 2)
    return "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1))


def _unsafe_violation_context(
    *,
    files: dict[str, str],
    issues: dict[str, list[str]],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for rel_path, labels in sorted(issues.items()):
        content = files.get(rel_path, "")
        lines = content.splitlines()
        for label in labels:
            line_number: int | None = None
            snippet = ""
            if label.startswith("write to reserved path:"):
                reserved_path = label.split(": ", 1)[1]
                for idx, line in enumerate(lines, start=1):
                    if reserved_path in line:
                        line_number = idx
                        snippet = _nearby_code_excerpt(lines=lines, line_number=line_number)
                        break
            else:
                for pattern, pattern_label in _UNSAFE_CODE_PATTERNS:
                    if pattern_label != label:
                        continue
                    match = pattern.search(content)
                    if match is None:
                        continue
                    line_number = content[: match.start()].count("\n") + 1
                    snippet = _nearby_code_excerpt(lines=lines, line_number=line_number)
                    break
            contexts.append(
                {
                    "path": rel_path,
                    "construct": label,
                    "line_number": line_number,
                    "snippet": snippet,
                }
            )
    return contexts


def _python_syntax_failure_detail(
    *,
    rel_path: str,
    source: str,
    intro: str,
    canonical_path: str | None = None,
) -> str | None:
    try:
        ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        line_number = exc.lineno
        offset = exc.offset
        lines = source.splitlines()
        failing_line = (exc.text or "").rstrip("\n")
        if not failing_line and line_number is not None and 1 <= line_number <= len(lines):
            failing_line = lines[line_number - 1]
        excerpt = _nearby_code_excerpt(lines=lines, line_number=line_number)
        detail_parts = [
            intro,
            f"filename={rel_path}",
        ]
        if canonical_path and canonical_path != rel_path:
            detail_parts.append(f"canonical_path={canonical_path}")
        detail_parts.append(f"error=SyntaxError: {exc.msg or str(exc)}")
        if line_number is not None:
            detail_parts.append(f"line_number={line_number}")
        if offset is not None:
            detail_parts.append(f"offset={offset}")
        if failing_line:
            detail_parts.append(f"failing_line={failing_line}")
        if excerpt:
            detail_parts.append("nearby_code_excerpt:\n" + excerpt)
        return "\n".join(detail_parts)
    return None


def _repair_candidate_rel_path(*, rel_path: str, attempt: int) -> str:
    normalized = rel_path.replace("\\", "/")
    suffix = normalized.removeprefix("generated/")
    return f"generated/repairs/attempt_{attempt}/{suffix}"


def _workspace_root_parent_index(test_rel_path: str) -> int:
    """Return Path(__file__).resolve().parents[N] for a generated test file."""
    normalized = test_rel_path.replace("\\", "/")
    return len(Path(normalized).parts) - 1


def _normalize_generated_test_workspace_root(content: str, test_rel_path: str) -> str:
    """Align WORKSPACE_ROOT depth when tests are staged under generated/repairs/..."""
    parent_index = _workspace_root_parent_index(test_rel_path)
    return re.sub(
        r"Path\(__file__\)\.resolve\(\)\.parents\[\d+\]",
        f"Path(__file__).resolve().parents[{parent_index}]",
        content,
    )


def _stage_repair_candidate_files(
    *,
    workspace: Path,
    files: dict[str, str],
    attempt: int,
) -> list[str]:
    candidate_paths: list[str] = []
    for rel_path, content in files.items():
        normalized = rel_path.replace("\\", "/")
        if not _validate_generated_path(normalized):
            raise ValueError(f"Model attempted to write disallowed path: {normalized}")
        candidate_rel = _repair_candidate_rel_path(rel_path=rel_path, attempt=attempt)
        if candidate_rel.endswith("/tests/test_agent.py") and "Path(__file__).resolve().parents[" in content:
            content = _normalize_generated_test_workspace_root(content, candidate_rel)
        candidate_abs = workspace / candidate_rel
        candidate_abs.parent.mkdir(parents=True, exist_ok=True)
        candidate_abs.write_text(content, encoding="utf-8")
        candidate_paths.append(candidate_rel)
    return sorted(candidate_paths)


def _load_repair_file_provenance(
    *,
    event_log: EventLog,
    session_id: UUID,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    latest = _latest_model_authoring_payload(event_log.read_all(session_id))
    raw_file_provenance = latest.get("file_provenance")
    file_provenance: dict[str, dict[str, Any]] = {}
    reference_hashes: dict[str, str] = {}
    if isinstance(raw_file_provenance, list):
        for entry in raw_file_provenance:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            file_provenance[path] = dict(entry)
            ref_hash = entry.get("reference_hash")
            if isinstance(ref_hash, str):
                reference_hashes[path] = ref_hash
    return file_provenance, reference_hashes


def _repair_candidate_syntax_failure(
    *,
    files: dict[str, str],
    attempt: int,
) -> str | None:
    for rel_path, content in sorted(files.items()):
        if not rel_path.replace("\\", "/").endswith(".py"):
            continue
        detail = _python_syntax_failure_detail(
            rel_path=_repair_candidate_rel_path(rel_path=rel_path, attempt=attempt),
            source=content,
            intro="Repair candidate failed Python syntax preflight before promotion.",
            canonical_path=rel_path.replace("\\", "/"),
        )
        if detail:
            return detail
    return None


def promote_repair_candidate_files(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    workspace: Path,
    attempt: int,
    failure_kind: str,
    candidate_paths: list[str],
    files: dict[str, str],
    provider: str | None,
    model: str | None,
) -> tuple[list[str], str | None, dict[str, dict[str, Any]]]:
    file_provenance, reference_hashes = _load_repair_file_provenance(
        event_log=event_log,
        session_id=session_id,
    )
    written, material_failure = _write_model_files(
        workspace=workspace,
        files=files,
        reference_hashes=reference_hashes,
        file_provenance=file_provenance,
    )
    if material_failure:
        return written, material_failure, file_provenance
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "repair_candidate_promoted",
            "stage": stage_label,
            "attempt": attempt,
            "failure_kind": failure_kind,
            "candidate_paths": candidate_paths,
            "promoted_files": written,
            "provider": provider or "unknown",
            "model": model or "unknown",
        },
        step=step,
    )
    refresh_model_authoring_summary(
        session_id=session_id,
        event_log=event_log,
        workspace=workspace,
        validation_result="pending_after_repair",
        output_artifacts=[],
    )
    return written, None, file_provenance


def finalize_repair_candidate_promotion(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    written: list[str],
    file_provenance: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], dict[str, Any]]:
    events = event_log.read_all(session_id)
    existing_files = model_contributed_files_from_events(events)
    contributed_files = sorted(set(existing_files + written))
    stages = collect_author_model_stages(events)
    if "execution_repair" not in stages:
        stages.append("execution_repair")
    summary_rel = "reports/model_authoring_summary.md"
    provenance = _record_authoring_provenance(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        contributed_files=contributed_files,
        stages_completed=stages,
        file_provenance=file_provenance,
        summary_rel=summary_rel,
    )
    return contributed_files, stages, provenance


async def _repair_json_payload(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    responses_dir: Path,
    source_stage: str,
    raw_text: str,
    expected_shape: str,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    instruction = (
        "Repair the malformed model output into valid JSON only. Do not add "
        "markdown fences, explanations, or new business logic."
    )
    if source_stage.startswith("safety_repair"):
        instruction = (
            "Repair only the outer JSON files envelope into valid JSON. Preserve the "
            "existing file paths and file contents exactly as they already appear in "
            "malformed_model_output. Do not rewrite code, do not remove or add logic, "
            "and do not attempt to fix safety issues here."
        )
    repair_prompt = json.dumps(
        {
            "stage": "json_repair",
            "source_stage": source_stage,
            "expected_shape": expected_shape,
            "malformed_model_output": raw_text,
            "instruction": instruction,
        },
        indent=2,
    )
    response, _call_error, response_duration_ms = await _call_model(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        system_prompt="You repair malformed JSON. Return valid JSON only.",
        user_prompt=repair_prompt,
        purpose="json_repair",
        settings=settings,
    )
    if response is None:
        return None
    _write_response_artifacts(
        responses_dir=responses_dir,
        stage=f"{source_stage}.json_repair",
        prompt=repair_prompt,
        response_text=response.text,
    )
    _record_model_call(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        response=response,
        purpose="json_repair",
        settings=settings,
        duration_ms=response_duration_ms,
    )
    payload = _extract_json_payload(response.text)
    if not isinstance(payload, dict):
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "json_parse_failed",
                "stage": stage_label,
                "source_stage": f"{source_stage}.json_repair",
                "detail": _parse_failure_detail(
                    stage=f"{source_stage}.json_repair",
                    purpose="json_repair",
                    response=response,
                    settings=settings,
                ),
            },
            step=step,
        )
    return payload


async def _extract_json_or_repair(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    responses_dir: Path,
    source_stage: str,
    raw_text: str,
    expected_shape: str,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    payload = _extract_json_payload(raw_text)
    if isinstance(payload, dict):
        return payload
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "json_parse_failed",
            "stage": stage_label,
            "source_stage": source_stage,
            "detail": (
                f"{source_stage} did not yield valid JSON before json_repair; "
                f"expected_shape={expected_shape}"
            ),
        },
        step=step,
    )
    return await _repair_json_payload(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        responses_dir=responses_dir,
        source_stage=source_stage,
        raw_text=raw_text,
        expected_shape=expected_shape,
        settings=settings,
    )


def _contract_defaults(
    *,
    workflow_type: str,
    schema_profile: dict[str, Any],
    output_contract_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(output_contract_hint or {})
    columns = list(schema_profile.get("columns") or hint.get("input_columns") or [])
    input_file = (
        hint.get("input_file")
        or schema_profile.get("agent_input_path")
        or schema_profile.get("normalized_input_path")
        or schema_profile.get("input_file")
        or "uploads/normalised_input.csv"
    )
    input_format = hint.get("input_format") or schema_profile.get("upload_format") or "csv"
    return {
        "workflow_type": hint.get("workflow_type") or workflow_type,
        "workflow_confidence": hint.get("workflow_confidence") or 0.75,
        "build_mode": hint.get("build_mode") or "llm_custom",
        "input_file": input_file,
        "input_format": input_format,
        "selected_sheet": hint.get("selected_sheet") or schema_profile.get("selected_sheet"),
        "normalized_input_path": hint.get("normalized_input_path")
        or schema_profile.get("normalized_input_path"),
        "primary_sheet": hint.get("primary_sheet") or schema_profile.get("primary_sheet"),
        "reference_sheets": list(hint.get("reference_sheets") or []),
        "requires_model_planning": True,
        "primary_row_key": hint.get("primary_row_key"),
        "row_level_output_file": hint.get("row_level_output_file") or "outputs/output.csv",
        "summary_output_files": list(hint.get("summary_output_files") or []),
        "exception_output_files": list(hint.get("exception_output_files") or []),
        "input_columns": columns,
        "output_columns": list(hint.get("output_columns") or columns),
        "required_output_columns": list(hint.get("required_output_columns") or []),
        "optional_output_columns": list(hint.get("optional_output_columns") or []),
        "output_column_semantics": list(hint.get("output_column_semantics") or []),
        "calculated_fields": list(hint.get("calculated_fields") or []),
        "formula_input_columns": list(hint.get("formula_input_columns") or []),
        "formula_output_columns": list(hint.get("formula_output_columns") or []),
        "tolerances": dict(hint.get("tolerances") or {}),
        "aggregation_specs": list(hint.get("aggregation_specs") or []),
        "summary_group_keys": list(hint.get("summary_group_keys") or []),
        "summary_metrics": list(hint.get("summary_metrics") or []),
        "exception_rules": list(hint.get("exception_rules") or []),
        "classification_rules": list(hint.get("classification_rules") or []),
        "validation_checks": list(hint.get("validation_checks") or []),
        "golden_comparison_requirement": hint.get("golden_comparison_requirement") or "skipped",
        "golden_output_path": hint.get("golden_output_path"),
        "skipped_checks": list(hint.get("skipped_checks") or ["golden_output"]),
        "warnings": list(hint.get("warnings") or []),
        "clarification_questions": list(hint.get("clarification_questions") or []),
        "unsupported_reasons": list(hint.get("unsupported_reasons") or []),
        "requested_deliverables": list(hint.get("requested_deliverables") or []),
        "preserve_row_count": bool(hint.get("preserve_row_count", True)),
        "allowed_enums": dict(hint.get("allowed_enums") or {}),
    }


def _contract_payload_from_model(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("author_output_contract", "reviewed_contract", "contract_plan", "contract"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    return payload if payload else None


_BUILD_MODE_VALUES: tuple[str, ...] = ("llm_custom", "clarification", "unsupported")


def _contract_file_profile_metadata(schema_profile: dict[str, Any]) -> dict[str, Any]:
    """Immutable upload facts the model must echo in AuthorOutputContract."""
    input_file = (
        schema_profile.get("agent_input_path")
        or schema_profile.get("normalized_input_path")
        or schema_profile.get("input_file")
        or "uploads/normalised_input.csv"
    )
    metadata: dict[str, Any] = {
        "input_file": input_file,
        "input_format": schema_profile.get("upload_format") or "csv",
    }
    selected_sheet = schema_profile.get("selected_sheet")
    if isinstance(selected_sheet, str) and selected_sheet:
        metadata["selected_sheet"] = selected_sheet
    row_count = schema_profile.get("row_count")
    if isinstance(row_count, int):
        metadata["row_count"] = row_count
    normalized_input_path = schema_profile.get("normalized_input_path")
    if isinstance(normalized_input_path, str) and normalized_input_path:
        metadata["normalized_input_path"] = normalized_input_path
    return metadata


def _known_profile_input_file(schema_profile: dict[str, Any]) -> str | None:
    """Return an explicitly known uploaded/normalised input path, if one exists."""
    for key in ("agent_input_path", "normalized_input_path", "input_file"):
        value = schema_profile.get(key)
        if isinstance(value, str) and value:
            return value
    return None


_SUPPORTED_INPUT_FORMATS: frozenset[str] = frozenset({"csv", "xlsx"})


def _known_profile_input_format(schema_profile: dict[str, Any]) -> str | None:
    upload_format = schema_profile.get("upload_format")
    if isinstance(upload_format, str) and upload_format in _SUPPORTED_INPUT_FORMATS:
        return upload_format
    return None


def _input_format_from_upload_path(path: str) -> str | None:
    suffix = Path(str(path).replace("\\", "/")).suffix.lower().lstrip(".")
    if suffix in _SUPPORTED_INPUT_FORMATS:
        return suffix
    return None


def _derive_unambiguous_input_format(
    *,
    payload: dict[str, Any],
    schema_profile: dict[str, Any],
) -> str | None:
    known_format = _known_profile_input_format(schema_profile)
    if known_format:
        return known_format
    for candidate in (
        payload.get("input_file"),
        _known_profile_input_file(schema_profile),
        schema_profile.get("original_upload_path"),
    ):
        if not isinstance(candidate, str) or not candidate:
            continue
        derived = _input_format_from_upload_path(candidate)
        if derived:
            return derived
    return None


_OUTPUT_ARTIFACT_NAMESPACE_REWRITES: tuple[tuple[str, str], ...] = (
    ("summaries/", "outputs/"),
    ("summary/", "outputs/"),
    ("exceptions/", "outputs/"),
)


def _normalize_contract_output_artifact_path(path: str) -> tuple[str, bool]:
    """Rewrite a narrow set of invalid output namespaces to outputs/."""
    normalized = str(path).replace("\\", "/").strip()
    if not normalized:
        return normalized, False
    if normalized.startswith(("uploads/", "generated/", "outputs/", "reports/")):
        return normalized, False
    for source_prefix, target_prefix in _OUTPUT_ARTIFACT_NAMESPACE_REWRITES:
        if normalized.startswith(source_prefix):
            return f"{target_prefix}{normalized[len(source_prefix):]}", True
    return normalized, False


def _normalize_contract_path_entry(entry: Any) -> tuple[Any, bool]:
    if isinstance(entry, str):
        new_path, changed = _normalize_contract_output_artifact_path(entry)
        return new_path, changed
    if isinstance(entry, dict):
        item = dict(entry)
        changed = False
        for key in ("path", "output_path"):
            value = item.get(key)
            if not isinstance(value, str):
                continue
            new_path, path_changed = _normalize_contract_output_artifact_path(value)
            if path_changed:
                item[key] = new_path
                changed = True
        return item, changed
    return entry, False


def _sanitize_contract_output_path_fields(
    data: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    changed = False
    row_output = data.get("row_level_output_file")
    if isinstance(row_output, str):
        new_path, path_changed = _normalize_contract_output_artifact_path(row_output)
        if path_changed:
            data["row_level_output_file"] = new_path
            changed = True
    for key in (
        "summary_output_files",
        "exception_output_files",
        "requested_deliverables",
        "missing_deliverables",
    ):
        entries = data.get(key)
        if not isinstance(entries, list):
            continue
        normalized_entries: list[Any] = []
        for entry in entries:
            normalized_entry, entry_changed = _normalize_contract_path_entry(entry)
            normalized_entries.append(normalized_entry)
            changed = changed or entry_changed
        data[key] = normalized_entries
    checks = data.get("validation_checks")
    if isinstance(checks, list):
        normalized_checks: list[Any] = []
        for check in checks:
            if isinstance(check, dict) and isinstance(check.get("output_file"), str):
                item = dict(check)
                new_path, path_changed = _normalize_contract_output_artifact_path(
                    item["output_file"]
                )
                if path_changed:
                    item["output_file"] = new_path
                    changed = True
                normalized_checks.append(item)
            else:
                normalized_checks.append(check)
        data["validation_checks"] = normalized_checks
    return data, changed


def _artifact_path_namespace_guidance() -> dict[str, Any]:
    return {
        "allowed_output_prefixes": ["outputs/", "reports/"],
        "forbidden_output_prefixes": [
            "summaries/",
            "summary/",
            "exceptions/",
            "validation/",
            "exception_reports/",
        ],
        "mapping_rules": [
            "summaries/<filename> -> outputs/<filename>",
            "summary/<filename> -> outputs/<filename>",
            "exceptions/<filename> -> outputs/<filename>",
        ],
        "planning_rules": [
            "Machine-readable row-level CSVs, exception files, and summary CSV/JSON "
            "artifacts must use outputs/.",
            "Workflow/business markdown reports must use reports/.",
            "Backend system validation reports are backend-owned under reports/.",
            "Do not use summaries/ or summary/ as output namespaces.",
        ],
        "repair_rule": (
            "For contract-declared output artifact paths only, rewrite summaries/, "
            "summary/, or exceptions/ to outputs/<filename>. Preserve outputs/, reports/, "
            "uploads/, and generated/ paths unchanged. Do not rewrite ambiguous "
            "invalid paths or input_file."
        ),
    }


def _deliverable_name_from_output_path(path: str) -> str | None:
    stem = Path(path).stem.strip()
    if not stem:
        return None
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", stem)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or None


def _sanitize_deliverable_entries(
    entries: Any,
) -> tuple[list[Any], bool]:
    """Repair unambiguous deliverable-shape defects without changing semantics."""
    if not isinstance(entries, list):
        return entries, False
    changed = False
    sanitized: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            sanitized.append(entry)
            continue
        output_path = entry.get("output_path")
        legacy_path = entry.get("path")
        if not isinstance(output_path, str) and not isinstance(legacy_path, str):
            sanitized.append(entry)
            continue
        normalized = dict(entry)
        if not isinstance(normalized.get("output_path"), str) and isinstance(legacy_path, str):
            normalized["output_path"] = legacy_path
            changed = True
        if "path" in normalized:
            normalized.pop("path", None)
            changed = True
        name = normalized.get("name")
        if not isinstance(name, str) or not name.strip():
            derived_name = _deliverable_name_from_output_path(
                str(normalized.get("output_path") or "")
            )
            if derived_name:
                normalized["name"] = derived_name
                changed = True
        sanitized.append(normalized)
    return sanitized, changed


def _sanitize_calculated_field_entries(entries: Any) -> tuple[list[Any], bool]:
    if not isinstance(entries, list):
        return entries, False
    changed = False
    sanitized: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            sanitized.append(entry)
            continue
        item = dict(entry)
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            name = str(item.get("name") or "").strip()
            formula = str(item.get("formula") or "").strip()
            if name:
                item["description"] = f"Derived field {name.replace('_', ' ')}"
                changed = True
            elif formula:
                item["description"] = "Derived calculated field"
                changed = True
        sanitized.append(item)
    return sanitized, changed


def _sanitize_exception_rule_entries(entries: Any) -> tuple[list[Any], bool]:
    if not isinstance(entries, list):
        return entries, False
    changed = False
    sanitized: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            sanitized.append(entry)
            continue
        item = dict(entry)
        if "issue_flag" in item:
            issue_flag_value = item.pop("issue_flag")
            changed = True
            if not item.get("output_column"):
                item["output_column"] = "issue_flag"
            if (
                isinstance(issue_flag_value, str)
                and issue_flag_value.strip()
                and (not isinstance(item.get("reason"), str) or not str(item.get("reason")).strip())
            ):
                item["reason"] = issue_flag_value.strip()
        sanitized.append(item)
    return sanitized, changed


_CONTRACT_ALTERNATE_DIALECT_SCALAR_ALIASES: dict[str, str] = {
    "row_level_output_path": "row_level_output_file",
    "summary_output_path": "summary_output_files",
    "exception_output_path": "exception_output_files",
}

_CONTRACT_ALTERNATE_DIALECT_LIST_ALIASES: dict[str, str] = {
    "calculated_field_definitions": "calculated_fields",
    "exception_rule_definitions": "exception_rules",
    "validation_check_definitions": "validation_checks",
    "summary_metric_definitions": "summary_metrics",
    "aggregation_keys": "summary_group_keys",
}

_CONTRACT_ALTERNATE_DIALECT_DICT_ALIASES: dict[str, str] = {
    "tolerance_definitions": "tolerances",
}

_CONTRACT_ALTERNATE_DIALECT_STRIP_FIELDS: frozenset[str] = frozenset(
    {
        "row_level_output_path",
        "summary_output_path",
        "exception_output_path",
        "calculated_field_definitions",
        "exception_rule_definitions",
        "validation_check_definitions",
        "summary_metric_definitions",
        "aggregation_keys",
        "tolerance_definitions",
    }
)

_GENERIC_REVIEW_PSEUDOCONTRACT_FIELDS: tuple[str, ...] = (
    "input_path",
    "output_path",
    "workflow_name",
    "row_count",
    "input_sheet_name",
    "input_column_names",
)

_CONTRACT_REFERENCE_FILL_FIELDS: tuple[str, ...] = (
    "workflow_type",
    "build_mode",
    "input_file",
    "input_columns",
    "output_columns",
    "required_output_columns",
    "optional_output_columns",
    "primary_row_key",
    "row_level_output_file",
    "summary_output_files",
    "exception_output_files",
    "summary_metrics",
    "requested_deliverables",
)

_OUTPUT_COLUMN_PRODUCER_KIND_ALIASES: dict[str, str] = {
    "calculated_field": "calculation",
}


def _contract_schema_dialect_guidance() -> dict[str, Any]:
    return {
        "forbidden_alternate_fields": sorted(_CONTRACT_ALTERNATE_DIALECT_STRIP_FIELDS),
        "forbidden_generic_review_fields": list(_GENERIC_REVIEW_PSEUDOCONTRACT_FIELDS),
        "canonical_field_mappings": {
            **_CONTRACT_ALTERNATE_DIALECT_SCALAR_ALIASES,
            **_CONTRACT_ALTERNATE_DIALECT_LIST_ALIASES,
            **_CONTRACT_ALTERNATE_DIALECT_DICT_ALIASES,
        },
        "planning_rules": [
            "The reviewed contract must use the exact AuthorOutputContract dialect.",
            "Use row_level_output_file, not row_level_output_path.",
            "Use calculated_fields, not calculated_field_definitions.",
            "Use exception_rules, validation_checks, summary_metrics, and "
            "summary_group_keys instead of *_definitions or aggregation_keys aliases.",
            "output_column_semantics must be a list of OutputColumnSemanticsSpec objects, "
            "not a dict keyed by column name.",
            "workflow_type must be present when required by the schema.",
            "Do not emit generic pseudo-contract review fields such as input_path, "
            "output_path, workflow_name, row_count, input_sheet_name, or "
            "input_column_names.",
            "Do not include implementation notes or alternate schema aliases.",
        ],
        "repair_rule": (
            "When validation_issue_details show extra_forbidden alternate dialect "
            "fields, map unambiguous aliases to canonical AuthorOutputContract fields "
            "and remove the alias keys. Convert output_column_semantics dict shapes "
            "to lists without inventing business semantics. Populate missing "
            "workflow_type only from contract_reference when unambiguous. Do not "
            "treat generic pseudo-contract fields such as input_path, output_path, "
            "workflow_name, row_count, input_sheet_name, or input_column_names as "
            "canonical contract fields."
        ),
    }


def _contract_finalization_guidance(
    *,
    payload: dict[str, Any],
    schema_profile: dict[str, Any],
    contract_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uploaded_input_columns = list(
        dict.fromkeys(
            str(column) for column in list(schema_profile.get("columns") or []) if str(column)
        )
    )
    planned_input_columns = list(
        dict.fromkeys(
            str(column)
            for column in list((contract_reference or {}).get("input_columns") or [])
            if str(column)
        )
    )
    candidate_input_columns = list(
        dict.fromkeys(
            str(column) for column in list(payload.get("input_columns") or []) if str(column)
        )
    )
    input_column_set = (
        set(uploaded_input_columns) | set(planned_input_columns) | set(candidate_input_columns)
    )
    invalid_non_arithmetic_formulas: list[dict[str, str]] = []
    for item in list(payload.get("calculated_fields") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        formula = str(item.get("formula") or "").strip()
        if not name or not formula:
            continue
        field_classification = _classify_output_column(
            name,
            input_columns=input_column_set,
            formula=formula,
        )
        if field_classification in _NON_ARITHMETIC_FIELD_TYPES:
            invalid_non_arithmetic_formulas.append(
                {
                    "name": name,
                    "classification": field_classification,
                    "formula": formula,
                }
            )
    return {
        "field_types": [
            "passthrough",
            "numeric_arithmetic",
            "date_derived",
            "boolean_flag",
            "status_label",
            "category_label",
            "reason_text",
            "confidence_score",
            "summary_metric",
            "unknown",
        ],
        "non_arithmetic_suffixes": list(_NON_ARITHMETIC_OUTPUT_SUFFIXES),
        "uploaded_input_columns": uploaded_input_columns,
        "planned_input_columns": planned_input_columns,
        "candidate_input_columns": candidate_input_columns,
        "invalid_non_arithmetic_formulas": invalid_non_arithmetic_formulas,
        "planning_rules": [
            "input_columns must come from the uploaded input schema. Do not invent raw source columns absent from schema_profile.",
            "Only numeric arithmetic derived outputs may use calculated_fields.formula.",
            "Non-arithmetic derived outputs such as *_date, *_status, *_reason, *_bucket, *_category, *_flag, *_ready, and *_required must use output_column_semantics rather than formula.",
        ],
        "review_rules": [
            "If the planned contract is already schema-valid, preserve the planned input column universe during review.",
            "Do not replace the workflow domain or introduce an unrelated input column universe during review.",
            "Repair only the invalid fields listed in validation_issue_details and preserve the rest of the reviewed contract unchanged.",
        ],
        "repair_rule": (
            "When finalisation reports formula_discipline, remove formulas only from clearly "
            "non-arithmetic derived fields and keep those fields defined via "
            "output_column_semantics. When finalisation reports input_columns drift or "
            "unknown input columns, restore the uploaded/planned input column universe "
            "instead of inventing new raw columns."
        ),
    }


def _normalize_output_column_producer_kind(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _OUTPUT_COLUMN_PRODUCER_KIND_ALIASES.get(value, value)


def _canonical_list_field_empty(value: Any) -> bool:
    return not isinstance(value, list) or len(value) == 0


def _map_alternate_scalar_path_field(
    data: dict[str, Any],
    *,
    alternate_key: str,
    canonical_key: str,
) -> bool:
    alternate_value = data.get(alternate_key)
    if not isinstance(alternate_value, str) or not alternate_value.strip():
        return False
    canonical_value = data.get(canonical_key)
    if isinstance(canonical_value, str) and canonical_value.strip():
        data.pop(alternate_key, None)
        return True
    if canonical_key == "summary_output_files" or canonical_key == "exception_output_files":
        data[canonical_key] = [alternate_value]
    else:
        data[canonical_key] = alternate_value
    data.pop(alternate_key, None)
    return True


def _map_alternate_list_field(
    data: dict[str, Any],
    *,
    alternate_key: str,
    canonical_key: str,
) -> bool:
    alternate_value = data.get(alternate_key)
    if alternate_value is None:
        return False
    canonical_value = data.get(canonical_key)
    if not _canonical_list_field_empty(canonical_value):
        data.pop(alternate_key, None)
        return True
    if isinstance(alternate_value, list):
        data[canonical_key] = alternate_value
        data.pop(alternate_key, None)
        return True
    return False


def _map_alternate_dict_field(
    data: dict[str, Any],
    *,
    alternate_key: str,
    canonical_key: str,
) -> bool:
    alternate_value = data.get(alternate_key)
    if not isinstance(alternate_value, dict):
        return False
    canonical_value = data.get(canonical_key)
    if isinstance(canonical_value, dict) and canonical_value:
        data.pop(alternate_key, None)
        return True
    data[canonical_key] = alternate_value
    data.pop(alternate_key, None)
    return True


def _convert_output_column_semantics_dict_to_list(
    semantics: Any,
) -> list[dict[str, Any]] | None:
    if not isinstance(semantics, dict):
        return None
    converted: list[dict[str, Any]] = []
    for name, spec in semantics.items():
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(spec, dict):
            return None
        item = {"name": name.strip(), **dict(spec)}
        item["producer_kind"] = _normalize_output_column_producer_kind(
            item.get("producer_kind")
        )
        converted.append(item)
    return converted


def _merge_output_column_semantics_entry(
    entry: dict[str, Any],
    reference_entry: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(entry)
    for field in (
        "description",
        "row_semantics",
        "fallback_value_semantics",
        "producer_kind",
        "required",
        "nullable",
        "allow_empty_string",
    ):
        current = merged.get(field)
        if current not in (None, ""):
            continue
        reference_value = reference_entry.get(field)
        if reference_value in (None, ""):
            continue
        if field == "producer_kind":
            merged[field] = _normalize_output_column_producer_kind(reference_value)
        else:
            merged[field] = reference_value
    return merged


def _output_column_semantics_reference_by_name(
    contract_reference: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(contract_reference, dict):
        return {}
    by_name: dict[str, dict[str, Any]] = {}
    for entry in contract_reference.get("output_column_semantics") or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"]:
            by_name[entry["name"]] = dict(entry)
    return by_name


def _sanitize_output_column_semantics_alternate_shape(
    payload: dict[str, Any],
    *,
    schema_profile: dict[str, Any],
    contract_reference: dict[str, Any] | None,
) -> bool:
    semantics = payload.get("output_column_semantics")
    if isinstance(semantics, list):
        normalized: list[Any] = []
        changed = False
        for entry in semantics:
            if not isinstance(entry, dict):
                normalized.append(entry)
                continue
            item = dict(entry)
            producer_kind = _normalize_output_column_producer_kind(item.get("producer_kind"))
            if producer_kind != item.get("producer_kind"):
                item["producer_kind"] = producer_kind
                changed = True
            normalized.append(item)
        if changed:
            payload["output_column_semantics"] = normalized
        return changed

    converted = _convert_output_column_semantics_dict_to_list(semantics)
    if converted is None:
        return False

    reference_by_name = _output_column_semantics_reference_by_name(contract_reference)
    known_input_columns = _known_input_columns(payload=payload, schema_profile=schema_profile)
    normalized_entries: list[dict[str, Any]] = []
    for entry in converted:
        name = str(entry.get("name") or "")
        reference_entry = reference_by_name.get(name)
        if reference_entry:
            entry = _merge_output_column_semantics_entry(entry, reference_entry)
        elif (
            entry.get("producer_kind") == "copied_input"
            and name in known_input_columns
            and not str(entry.get("row_semantics") or "").strip()
        ):
            passthrough = _passthrough_output_column_semantics_spec(name)
            for field, value in passthrough.items():
                if field not in entry or entry[field] in (None, ""):
                    entry[field] = value
        normalized_entries.append(entry)

    required_columns = list(payload.get("required_output_columns") or [])
    if not required_columns and isinstance(contract_reference, dict):
        required_columns = list(contract_reference.get("required_output_columns") or [])
    existing_names = {
        str(entry.get("name"))
        for entry in normalized_entries
        if isinstance(entry, dict) and entry.get("name")
    }
    for column in required_columns:
        if column in existing_names:
            continue
        reference_entry = reference_by_name.get(column)
        if reference_entry:
            normalized_entries.append(dict(reference_entry))
            existing_names.add(column)

    payload["output_column_semantics"] = normalized_entries
    return True


def _apply_missing_contract_reference_fields(
    payload: dict[str, Any],
    contract_reference: dict[str, Any] | None,
) -> bool:
    if not isinstance(contract_reference, dict):
        return False
    changed = False
    for field in _CONTRACT_REFERENCE_FILL_FIELDS:
        current = payload.get(field)
        reference_value = contract_reference.get(field)
        if reference_value in (None, "", []):
            continue
        if field in {
            "input_columns",
            "output_columns",
            "required_output_columns",
            "optional_output_columns",
            "summary_output_files",
            "exception_output_files",
            "summary_metrics",
            "requested_deliverables",
        }:
            if not _canonical_list_field_empty(current):
                continue
        elif isinstance(current, str):
            if current.strip():
                continue
        elif current not in (None, ""):
            continue
        payload[field] = reference_value
        changed = True
    return changed


def _sanitize_contract_alternate_dialect_payload(
    payload: dict[str, Any],
    *,
    schema_profile: dict[str, Any],
    contract_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize unambiguous review-time alternate schema dialect aliases."""
    data = dict(payload)
    changed = False

    for alternate_key, canonical_key in _CONTRACT_ALTERNATE_DIALECT_SCALAR_ALIASES.items():
        if _map_alternate_scalar_path_field(
            data,
            alternate_key=alternate_key,
            canonical_key=canonical_key,
        ):
            changed = True

    for alternate_key, canonical_key in _CONTRACT_ALTERNATE_DIALECT_LIST_ALIASES.items():
        if _map_alternate_list_field(
            data,
            alternate_key=alternate_key,
            canonical_key=canonical_key,
        ):
            changed = True

    for alternate_key, canonical_key in _CONTRACT_ALTERNATE_DIALECT_DICT_ALIASES.items():
        if _map_alternate_dict_field(
            data,
            alternate_key=alternate_key,
            canonical_key=canonical_key,
        ):
            changed = True

    for field in _CONTRACT_ALTERNATE_DIALECT_STRIP_FIELDS:
        if field in data:
            data.pop(field, None)
            changed = True

    if _apply_missing_contract_reference_fields(data, contract_reference):
        changed = True

    if _sanitize_output_column_semantics_alternate_shape(
        data,
        schema_profile=schema_profile,
        contract_reference=contract_reference,
    ):
        changed = True

    return data if changed else payload


def _sanitize_contract_shape_payload(
    *,
    payload: dict[str, Any],
    schema_profile: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Repair a narrow set of schema-shape defects before strict validation.

    This only fixes unambiguous contract-shape issues:
    - populate missing/invalid input_file from known upload metadata
    - populate missing/invalid input_format from known upload metadata or extension
    - remove forbidden top-level prompt-leakage/meta fields
    - remove forbidden top-level output_format
    - normalize DeliverableSpec path -> output_path and derive a deterministic name
    """

    data = dict(payload)
    changed = False

    if "output_format" in data:
        data.pop("output_format", None)
        changed = True
    for key in _CONTRACT_FORBIDDEN_TOP_LEVEL_FLAGS:
        if key in data:
            data.pop(key, None)
            changed = True
    for key in _CONTRACT_FORBIDDEN_TOP_LEVEL_META_FIELDS:
        if key in data:
            data.pop(key, None)
            changed = True

    known_input_file = _known_profile_input_file(schema_profile)
    input_file = data.get("input_file")
    if known_input_file and (
        not isinstance(input_file, str)
        or not input_file
        or not input_file.startswith(("uploads/", "generated/data/"))
    ):
        data["input_file"] = known_input_file
        changed = True

    input_format = data.get("input_format")
    if not isinstance(input_format, str) or input_format not in _SUPPORTED_INPUT_FORMATS:
        derived_format = _derive_unambiguous_input_format(
            payload=data,
            schema_profile=schema_profile,
        )
        if derived_format:
            data["input_format"] = derived_format
            changed = True

    for key in ("requested_deliverables", "missing_deliverables"):
        sanitized_entries, entries_changed = _sanitize_deliverable_entries(data.get(key))
        if entries_changed:
            data[key] = sanitized_entries
            changed = True
    calculated_fields, calc_changed = _sanitize_calculated_field_entries(
        data.get("calculated_fields")
    )
    if calc_changed:
        data["calculated_fields"] = calculated_fields
        changed = True
    exception_rules, exception_changed = _sanitize_exception_rule_entries(
        data.get("exception_rules")
    )
    if exception_changed:
        data["exception_rules"] = exception_rules
        changed = True

    data, path_changed = _sanitize_contract_output_path_fields(data)
    if path_changed:
        changed = True

    return data, changed


def _contract_top_level_skeleton(
    *,
    schema_profile: dict[str, Any],
    workflow_type: str,
) -> dict[str, Any]:
    """Exact top-level AuthorOutputContract skeleton for contract planning prompts."""
    profile = _contract_file_profile_metadata(schema_profile)
    columns = schema_profile.get("columns") or []
    return {
        "build_mode": "llm_custom",
        "workflow_type": workflow_type,
        "workflow_confidence": 0.0,
        "input_file": profile["input_file"],
        "input_format": profile["input_format"],
        "selected_sheet": profile.get("selected_sheet"),
        "normalized_input_path": profile.get("normalized_input_path"),
        "row_level_output_file": "outputs/output.csv",
        "summary_output_files": [],
        "exception_output_files": [],
        "input_columns": columns,
        "output_columns": [],
        "primary_row_key": None,
        "required_output_columns": [],
        "optional_output_columns": [],
        "output_column_semantics": [],
        "calculated_fields": [],
        "formula_input_columns": [],
        "formula_output_columns": [],
        "summary_group_keys": [],
        "summary_metrics": [],
        "exception_rules": [],
        "allowed_enums": {},
        "validation_checks": [],
        "skipped_checks": [],
        "clarification_questions": [],
        "unsupported_reasons": [],
        "requested_deliverables": [],
        "produced_deliverables": [],
        "missing_deliverables": [],
        "preserve_row_count": True,
    }


def _contract_schema_example() -> dict[str, Any]:
    return {
        "workflow_type": "short_snake_case_name",
        "workflow_confidence": 0.0,
        "build_mode": "llm_custom",
        "input_file": "uploads/input.csv",
        "input_format": "csv",
        "row_level_output_file": "outputs/output.csv",
        "summary_output_files": [
            {
                "path": "outputs/summary_by_batch.csv",
                "description": "Grouped totals by batch key",
                "required_columns": ["batch_id", "row_count", "amount_total"],
            }
        ],
        "exception_output_files": [
            {
                "path": "outputs/exceptions.csv",
                "description": "Rows flagged for review",
                "required_columns": ["row_id", "issue_flag", "issue_reason"],
            }
        ],
        "input_columns": ["col_a", "amount"],
        "output_columns": ["col_a", "amount", "issue_flag"],
        "required_output_columns": ["issue_flag"],
        "output_column_semantics": [
            {
                "name": "issue_flag",
                "description": "Non-empty review flag for each output row",
                "producer_kind": "exception_flag",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Emit a non-empty row-level review flag for every output row.",
                "fallback_value_semantics": "Use an explicit default flag value even when no exception condition matches.",
            }
        ],
        "calculated_fields": [{"name": "net_amount", "description": "desc", "formula": "amount"}],
        "exception_rules": [
            {
                "name": "amount_mismatch",
                "condition": "abs(expected_amount - actual_amount) > tolerance",
                "reason": "Expected and actual amounts differ",
                "severity": "high",
                "output_column": "issue_flag",
            }
        ],
        "validation_checks": [
            {
                "check_id": "row_count",
                "layer": "row_level",
                "name": "Row count preserved",
                "required": True,
                "check_type": "row_count",
            },
            {
                "check_id": "net_amount_formula",
                "layer": "business_rules",
                "name": "Net amount formula",
                "required": True,
                "check_type": "formula",
                "formula": "net_amount == amount",
                "output_column": "net_amount",
                "input_columns": ["amount"],
                "tolerance": 0.01,
            },
        ],
        "allowed_enums": {
            "issue_flag": ["no_issue", "review_required"],
        },
        "summary_group_keys": ["batch_id"],
        "summary_metrics": [
            "row_count",
            {
                "name": "amount_total",
                "metric_type": "sum",
                "source_column": "amount",
                "description": "Total amount grouped by batch_id",
            },
            {
                "name": "category_counts",
                "metric_type": "count",
                "source_column": "category",
                "group_by": ["category"],
                "description": "Count rows by category",
                "input_columns": ["category"],
                "output_column": "category_count",
            },
            {
                "name": "uncertain_rows_count",
                "metric_type": "count",
                "source_column": "confidence",
                "filter": "confidence < 0.8",
                "description": "Count rows with low confidence",
                "input_columns": ["confidence"],
                "output_column": "uncertain_rows_count",
            },
        ],
        "requested_deliverables": [
            "outputs/output.csv",
            {
                "name": "validation_report",
                "description": "Validation report explaining rules and counts",
                "output_path": "reports/validation_report.md",
                "required": True,
            },
        ],
        "missing_deliverables": [],
        "skipped_checks": [],
        "clarification_questions": [],
        "unsupported_reasons": [],
        "preserve_row_count": True,
    }


def _contract_prompt_rules() -> list[str]:
    return [
        "Return JSON only. No markdown fences or prose outside JSON.",
        "Use exact AuthorOutputContract keys only. No extra keys.",
        (
            'build_mode must be exactly one of "llm_custom", "clarification", or '
            '"unsupported". Do not invent build_mode values such as '
            '"model_authored_finance_workflow".'
        ),
        (
            "Echo required_file_profile_fields exactly for input_file, input_format, "
            "and selected_sheet when present. These are immutable upload facts supplied "
            "by the backend — not business logic you may invent."
        ),
        'input_format must be exactly the profiled upload format: "csv" or "xlsx".',
        "input_columns must come from the uploaded input schema. Do not invent raw "
        "source columns that are absent from schema_profile.",
        "If unsure about workflow intent or prompt/file alignment, set build_mode to "
        '"clarification" and populate clarification_questions instead of guessing.',
        "summary_output_files and exception_output_files may be plain path strings "
        "or structured OutputFileSpec objects with path, description, required_columns, "
        "and optional_columns.",
        "output_column_semantics may contain OutputColumnSemanticsSpec objects with "
        "name, description, producer_kind, required, nullable, allow_empty_string, "
        "row_semantics, and fallback_value_semantics.",
        "calculated_fields may contain CalculatedFieldSpec objects with name, "
        "description, and optional formula. Every calculated field requires name "
        "and description.",
        "exception_rules may be plain strings or structured ExceptionRuleSpec objects "
        "with name, condition, reason, severity, and optional output_column.",
        "ExceptionRuleSpec does not allow issue_flag as a nested field. If a rule "
        "writes to an issue_flag output column, use output_column='issue_flag' and "
        "keep the human-readable explanation in reason.",
        "validation_checks require check_id, layer, name, and required. For formula "
        "checks set check_type to formula and include formula plus any input_columns, "
        "output_column, and tolerance fields.",
        (
            "ValidationCheckSpec allows only: check_id, layer, name, required, "
            "check_type, output_file, formula, input_columns, output_column, tolerance, "
            "severity. Never put allowed_enums, allowed_values, categories, min_value, "
            "max_value, minimum, maximum, enum_values, range, bounds, or thresholds "
            "inside validation_checks."
        ),
        (
            "allowed_enums belongs only in top-level AuthorOutputContract.allowed_enums. "
            "Category membership constraints must use top-level allowed_enums — do not "
            "duplicate enum lists inside validation_checks."
        ),
        (
            "Numeric range constraints must not use min_value or max_value. Represent "
            "hard range gates with a formula-based validation_checks entry "
            "(check_type formula) using only allowed ValidationCheckSpec fields, or "
            "omit the unsupported structured range check."
        ),
        "validation_checks are hard gates when required=true. Do not create a hard "
        "validation check that contradicts the user prompt, allowed_enums, or "
        "output_column_semantics fallback_value_semantics.",
        "If the user prompt explicitly lists allowed labels, statuses, categories, "
        "or classes for an output column, put that list in allowed_enums for the "
        "column and validate membership instead of forbidding one allowed value. "
        "When the user prompt contains an explicit closed category list "
        '(for example "standard categories A, B, C" or "classify into"), '
        "populate allowed_enums.category with that exact closed list. "
        "Do not replace prompt-provided category labels with inferred vendor names, "
        "keyword labels, or alternative category vocabularies. "
        "If allowed_enums or fallback_value_semantics permits a value, a required "
        "validation check must not reject that same value unless the user explicitly "
        "says it is invalid.",
        "Do not convert advisory/reporting requirements such as counts of uncertain, "
        "fallback, or human-review rows into hard validation failures. Represent "
        "those as summary_metrics, report semantics, or non-required checks.",
        "calculated_fields items require name and description. Only include formula "
        f"when it fits the {_FORMULA_DSL_DESCRIPTION}. Do not use pseudo-function "
        "calls or natural-language descriptions as formulas.",
        "Non-arithmetic derived outputs such as *_date, *_status, *_reason, "
        "*_bucket, *_category, *_flag, *_ready, *_required, and other "
        "date/status/category/reason/boolean outputs must use "
        "output_column_semantics rather than calculated_fields.formula.",
        "summary_metrics may be plain metric-name strings or structured "
        "SummaryMetricSpec objects with name, metric_type (sum, count, average, min, "
        "max, or custom), optional source_column, group_by, filter, description, "
        "formula, input_columns, and output_column. Use summary_group_keys for shared "
        "grouping columns across summary metrics.",
        "SummaryMetricSpec does not allow a `condition` field. Use `filter` for row "
        "selection semantics or `formula` only when the metric meaning genuinely "
        "belongs in that field.",
        "requested_deliverables and missing_deliverables may be plain path strings "
        "or structured DeliverableSpec objects with name, description, optional "
        "output_path, required, and status.",
        "Structured DeliverableSpec objects must use output_path, not path. If you "
        "return a structured deliverable object, include name and use only the "
        "allowed DeliverableSpec fields.",
        "Only mark a deliverable as required when the user request or file profile "
        "directly justifies that artifact. Use DeliverableSpec with required=true and "
        "source='user_explicit' only when the user explicitly asked for a separate file. "
        "Plain path strings in requested_deliverables are informational only and never "
        "imply required=true.",
        "Do not convert report contents (category counts, uncertain rows, human-review "
        "rows, exception summaries) into separate required CSV files unless the user "
        "explicitly requested separate artifacts.",
        "Separate exception files are valid required deliverables when the prompt "
        "explicitly asks for a separate exceptions file; use source='user_explicit'.",
        "Summary CSV or JSON files are valid required deliverables when the prompt "
        "explicitly asks to produce, export, or summarise a separate summary artifact; "
        "use source='user_explicit'.",
        "Helpful extra summary or exception files may be declared in summary_output_files "
        "or exception_output_files, but keep them out of required requested_deliverables "
        "unless the user explicitly asked for a separate artifact.",
        "Do not infer a separate exception CSV solely from a request to explain "
        "uncertain rows or human-review rows in a report.",
        "Every required_output_columns entry must also appear in output_column_semantics "
        "with required=true, nullable=false, allow_empty_string=false, a concrete "
        "row_semantics definition, and a non-empty fallback_value_semantics string.",
        "Do not list a column as required unless its output_column_semantics are defined.",
        "Do not omit output_column_semantics for passthrough columns merely because they already exist in input_columns.",
        "If a required output column is copied unchanged from input_columns, use "
        "producer_kind copied_input and describe it as copied unchanged for "
        "traceability rather than inventing calculation logic.",
        "If a required output column is not present in input_columns, do not mark "
        "it as copied_input. Define explicit derived row semantics using "
        "calculated_fields, exception_rules, classification_rules, or the user request.",
        "Only place passthrough input columns in required_output_columns when the "
        "workflow truly requires them as non-null output fields; otherwise keep "
        "them in output_columns for traceability only.",
        "Treat missing values, None, empty strings, and whitespace-only strings as "
        "invalid when allow_empty_string=false.",
        "If a derived row-level field cannot be expressed with the supported "
        f"calculated_fields formula DSL ({_FORMULA_DSL_DESCRIPTION}), omit the "
        "formula and define the field through output_column_semantics plus any "
        "classification or exception rules instead.",
        "If multiple required explanation or audit columns exist, define their "
        "row-level meanings explicitly in output_column_semantics so codegen can "
        "populate each column without ambiguity.",
        "If you declare an extra summary or exception file as required, include that "
        "exact path in requested_deliverables with required=true.",
        "Do not emit output_format. It is not an AuthorOutputContract field.",
        "Do not emit preserve_original_data, enable_logging, or enable_debugging. "
        "They are not AuthorOutputContract fields.",
        "Use row_level_output_file, not row_level_output_path.",
        "Use calculated_fields, not calculated_field_definitions.",
        "Do not emit generic pseudo-contract review fields such as input_path, "
        "output_path, workflow_name, row_count, input_sheet_name, or "
        "input_column_names.",
        "If the planned contract is already schema-valid, review must preserve the "
        "planned input column universe and workflow domain instead of replacing it "
        "with unrelated raw source columns.",
        "Use exception_rules, validation_checks, summary_metrics, and summary_group_keys "
        "instead of *_definitions or aggregation_keys aliases.",
        "output_column_semantics must be a list of OutputColumnSemanticsSpec objects, "
        "not a dict keyed by column name.",
        "workflow_type must be present when required by the schema.",
        (
            "Contract-declared output artifact paths must use outputs/ for row-level "
            "CSVs, exception files, and machine-readable summary CSV/JSON files, and "
            "reports/ for workflow/business markdown reports. Do not use summaries/, "
            "summary/, exceptions/, validation/, or exception_reports/ as output namespaces."
        ),
        "Output paths must live under outputs/ or reports/. Input file must live under uploads/ "
        "or generated/data/.",
        "If the user prompt and uploaded file profile describe materially different "
        "finance domains, do not force a contract. Set build_mode to clarification and "
        "populate clarification_questions with one plain-English question explaining "
        "the mismatch.",
        (
            "allowed_enums must map output column names to plain JSON arrays. Do not "
            "wrap enum lists inside values, allowed_values, enum_values, or categories."
        ),
        (
            "Return only AuthorOutputContract fields. Do not include prompt guidance, "
            "schema paths, file-pattern metadata, or required_file_profile_fields as "
            "top-level contract keys."
        ),
    ]


def _extract_planning_clarifications(
    *,
    planning_payload: dict[str, Any] | None,
    contract_payload: dict[str, Any] | None,
) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    seen: set[str] = set()
    for source in (planning_payload, contract_payload):
        if not isinstance(source, dict):
            continue
        raw = source.get("clarification_questions")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                try:
                    question = ClarificationQuestion.model_validate(item)
                except ValidationError:
                    continue
            elif isinstance(item, str) and item.strip():
                question = ClarificationQuestion(
                    question=item.strip(),
                    reason="The model needs clarification before authoring a contract.",
                )
            else:
                continue
            if question.question in seen:
                continue
            seen.add(question.question)
            questions.append(question)
    return questions


def _contract_clarification_result(
    question: ClarificationQuestion,
    *,
    stages: list[str],
) -> ModelAuthoringResult:
    return ModelAuthoringResult(
        ok=False,
        error_code=ErrorCode.AUTHOR_CONTRACT_CLARIFICATION_REQUIRED,
        message=question.question,
        technical_detail=question.reason,
        stages=stages,
    )


def _contract_planning_failed_result(
    *,
    technical_detail: str | None,
    stages: list[str],
) -> ModelAuthoringResult:
    return ModelAuthoringResult(
        ok=False,
        error_code=ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
        message=(
            "The model could not produce a valid workflow contract. "
            "The model returned output file, rule, or check structures that did not "
            "match the allowed contract schema. No code was generated and no output "
            "was produced. Try clarifying the workflow or using a stronger model."
        ),
        technical_detail=technical_detail,
        stages=stages,
    )


def _contract_review_failed_result(
    *,
    technical_detail: str | None,
    stages: list[str],
) -> ModelAuthoringResult:
    return ModelAuthoringResult(
        ok=False,
        error_code=ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED,
        message=(
            "The model could not produce a valid reviewed workflow contract. "
            "The reviewed contract did not match the allowed contract schema. "
            "No code was generated and no output was produced. Try clarifying the "
            "workflow or using a stronger model."
        ),
        technical_detail=technical_detail,
        stages=stages,
    )


def _validate_contract_path_prefix(path: str) -> bool:
    normalized = str(path).replace("\\", "/")
    return normalized.startswith(("outputs/", "reports/"))


def _validate_contract_paths(contract: AuthorOutputContract) -> None:
    if not contract.input_file.startswith(("uploads/", "generated/data/")):
        raise ValueError("input_file must be under uploads/ or generated/data/")
    for path in contract.all_declared_output_paths():
        if not _validate_contract_path_prefix(path):
            raise ValueError(f"output path must be under outputs/ or reports/: {path}")


def _validate_required_output_column_semantics(contract: AuthorOutputContract) -> None:
    by_name = contract.output_column_semantics_by_name()
    missing: list[str] = []
    issues: list[str] = []
    for column in contract.required_output_columns:
        spec = by_name.get(column)
        if spec is None:
            missing.append(column)
            continue
        if not spec.required:
            issues.append(f"{column}: required must be true")
        if spec.nullable:
            issues.append(f"{column}: nullable must be false")
        if spec.allow_empty_string:
            issues.append(f"{column}: allow_empty_string must be false")
        if not spec.row_semantics.strip():
            issues.append(f"{column}: row_semantics must be non-empty")
        if not spec.fallback_value_semantics.strip():
            issues.append(f"{column}: fallback_value_semantics must be non-empty")
    if missing or issues:
        detail: list[str] = []
        if missing:
            detail.append(
                "missing output_column_semantics for required columns: "
                + ", ".join(missing)
            )
        if issues:
            detail.append("invalid required output column semantics: " + "; ".join(issues))
        raise ValueError(
            "required_output_columns need explicit non-null row semantics and fallback "
            "semantics via output_column_semantics; " + " | ".join(detail)
        )


_NON_ARITHMETIC_OUTPUT_SUFFIXES: tuple[str, ...] = (
    "_date",
    "_status",
    "_reason",
    "_bucket",
    "_category",
    "_flag",
    "_ready",
    "_required",
)

_NON_ARITHMETIC_FIELD_TYPES = frozenset(
    {
        "date_derived",
        "boolean_flag",
        "status_label",
        "category_label",
        "reason_text",
        "confidence_score",
    }
)


def _humanize_identifier(text: str) -> str:
    tokens = [token for token in re.split(r"[_\s]+", text.strip()) if token]
    if not tokens:
        return text.strip() or "value"
    return " ".join(token.capitalize() for token in tokens)


def _is_passthrough_input_column(
    column: str,
    *,
    input_columns: set[str],
) -> bool:
    return column in input_columns


def _classify_output_column(
    column: str,
    *,
    input_columns: set[str],
    formula: str | None = None,
) -> str:
    lower = column.strip().lower()
    if _is_passthrough_input_column(column, input_columns=input_columns):
        return "passthrough"
    if lower.startswith("confidence") or lower.endswith("_confidence") or lower == "confidence":
        return "confidence_score"
    if lower.endswith("_status") or lower == "status":
        return "status_label"
    if lower.endswith("_reason") or lower == "reason":
        return "reason_text"
    if lower.endswith("_bucket") or lower.endswith("_category") or lower == "category":
        return "category_label"
    if (
        lower.endswith("_flag")
        or lower.startswith("is_")
        or lower.startswith("has_")
        or lower.endswith("_ready")
        or lower.endswith("_required")
    ):
        return "boolean_flag"
    if lower.endswith("_date") or lower == "date":
        return "date_derived"
    if formula:
        with suppress(ValueError):
            validate_supported_formula(formula)
            return "numeric_arithmetic"
    return "unknown"


def _derived_output_column_semantics_spec(
    column: str,
    *,
    description: str,
    field_classification: str,
) -> dict[str, Any]:
    spec = dict(_output_column_semantics_examples()["required_derived_output_column"])
    spec["name"] = column
    spec["description"] = description.strip() or _humanize_identifier(column)

    if field_classification == "date_derived":
        spec["producer_kind"] = "other"
        spec["row_semantics"] = (
            "Populate the derived date value for each output row using the workflow's "
            "documented non-arithmetic date logic."
        )
        spec["fallback_value_semantics"] = (
            "Use the workflow's explicit default or normalization rule so the derived "
            "date field is never left blank."
        )
    elif field_classification == "status_label":
        spec["producer_kind"] = "classification"
        spec["row_semantics"] = (
            "Populate the derived status label for each output row using the workflow's "
            "documented non-arithmetic status logic."
        )
        spec["fallback_value_semantics"] = (
            "Use the workflow's explicit default or normalization rule so the derived "
            "status label is never left blank."
        )
    elif field_classification == "category_label":
        spec["producer_kind"] = "classification"
        spec["row_semantics"] = (
            "Populate the derived bucket or category label for each output row using "
            "the workflow's documented non-arithmetic assignment logic."
        )
        spec["fallback_value_semantics"] = (
            "Use the workflow's explicit default or normalization rule so the derived "
            "bucket/category label is never left blank."
        )
    elif field_classification == "reason_text":
        spec["producer_kind"] = "rule_explanation"
        spec["row_semantics"] = (
            "Populate the derived explanatory reason text for each output row using "
            "the workflow's documented review or classification logic."
        )
        spec["fallback_value_semantics"] = (
            "Use the workflow's explicit default or normalization rule so the reason "
            "text is never left blank."
        )
    elif field_classification == "boolean_flag":
        spec["producer_kind"] = "exception_flag" if column.lower().endswith("_flag") else "other"
        spec["row_semantics"] = (
            "Populate the derived flag value for each output row using the workflow's "
            "documented review or classification logic."
        )
        spec["fallback_value_semantics"] = (
            "Use the workflow's explicit default or normalization rule so the flag "
            "value is never left blank."
        )
    elif field_classification == "confidence_score":
        spec["producer_kind"] = "other"
        spec["row_semantics"] = (
            "Populate the derived confidence score for each output row using the "
            "workflow's documented scoring logic."
        )
        spec["fallback_value_semantics"] = (
            "Use the workflow's explicit default or normalization rule so the "
            "confidence score is never left blank."
        )
    return spec


def _sanitize_formula_disciplined_calculated_fields_payload(
    payload: dict[str, Any],
    *,
    schema_profile: dict[str, Any],
) -> dict[str, Any]:
    """Remove clearly non-arithmetic formulas and move them to output semantics.

    This is limited to unambiguous cases where the output field classification makes
    formula placement invalid regardless of business domain. Ambiguous unsupported
    formulas remain for strict validation/repair.
    """

    data = dict(payload)
    calculated_fields = data.get("calculated_fields")
    if not isinstance(calculated_fields, list):
        return data

    input_columns = _known_input_columns(payload=data, schema_profile=schema_profile)
    output_columns = set(str(column) for column in list(data.get("output_columns") or []) if str(column))
    required_output_columns = set(
        str(column) for column in list(data.get("required_output_columns") or []) if str(column)
    )
    semantics = list(data.get("output_column_semantics") or [])
    semantics_by_name = {
        str(item.get("name")): item
        for item in semantics
        if isinstance(item, dict) and item.get("name")
    }
    formula_output_columns = list(data.get("formula_output_columns") or [])

    changed = False
    sanitized_fields: list[Any] = []
    for field in calculated_fields:
        if not isinstance(field, dict):
            sanitized_fields.append(field)
            continue
        normalized = dict(field)
        name = str(normalized.get("name") or "").strip()
        formula = str(normalized.get("formula") or "").strip()
        if not name or not formula:
            sanitized_fields.append(normalized)
            continue
        field_classification = _classify_output_column(
            name,
            input_columns=input_columns,
            formula=formula,
        )
        if field_classification not in _NON_ARITHMETIC_FIELD_TYPES:
            sanitized_fields.append(normalized)
            continue
        normalized["formula"] = None
        changed = True
        if name in formula_output_columns:
            formula_output_columns = [column for column in formula_output_columns if column != name]
        if (
            name not in semantics_by_name
            and (name in output_columns or name in required_output_columns)
        ):
            semantics_spec = _derived_output_column_semantics_spec(
                name,
                description=str(normalized.get("description") or "").strip(),
                field_classification=field_classification,
            )
            semantics.append(semantics_spec)
            semantics_by_name[name] = semantics_spec
        sanitized_fields.append(normalized)

    if not changed:
        return data
    data["calculated_fields"] = sanitized_fields
    if formula_output_columns != list(data.get("formula_output_columns") or []):
        data["formula_output_columns"] = formula_output_columns
    if semantics != list(data.get("output_column_semantics") or []):
        data["output_column_semantics"] = semantics
    return data


def _formula_identifier_names(formula: str) -> set[str]:
    try:
        parsed = ast.parse(formula, mode="eval")
    except SyntaxError:
        return set()
    return {
        node.id
        for node in ast.walk(parsed)
        if isinstance(node, ast.Name) and node.id
    }


def _validate_calculated_field_formulas(
    contract: AuthorOutputContract,
    *,
    schema_profile: dict[str, Any] | None = None,
) -> None:
    input_columns = set(schema_profile.get("columns") or []) if schema_profile else set(contract.input_columns)
    issues: list[dict[str, Any]] = []
    for index, field in enumerate(contract.calculated_fields):
        formula = str(field.formula or "").strip()
        if not formula:
            continue
        field_classification = _classify_output_column(
            field.name,
            input_columns=input_columns,
            formula=formula,
        )
        if field_classification in _NON_ARITHMETIC_FIELD_TYPES:
            issues.append(
                {
                    "path": f"calculated_fields[{index}].formula",
                    "type": "formula_discipline",
                    "field_name": field.name,
                    "field_classification": field_classification,
                    "formula": formula,
                    "message": (
                        f"calculated_fields[{index}].formula uses formula semantics for "
                        f"non-arithmetic field {field.name!r} ({field_classification}). "
                        "Non-arithmetic date/status/category/reason/flag/confidence "
                        "outputs must use output_column_semantics instead of formula."
                    ),
                }
            )
            continue
        try:
            validate_supported_formula(formula)
        except ValueError as exc:
            issues.append(
                {
                    "path": f"calculated_fields[{index}].formula",
                    "type": "formula_syntax",
                    "field_name": field.name,
                    "formula": formula,
                    "message": (
                        "calculated_fields formulas must use the "
                        f"{_FORMULA_DSL_DESCRIPTION}; {field.name}: {exc}. "
                        "For non-arithmetic derived fields, omit formula and use "
                        "output_column_semantics row_semantics/fallback_value_semantics instead."
                    ),
                }
            )
    if issues:
        raise ContractSemanticValidationError(issues)


def _validate_input_column_faithfulness(
    contract: AuthorOutputContract,
    *,
    schema_profile: dict[str, Any] | None = None,
    contract_reference: dict[str, Any] | None = None,
) -> None:
    uploaded_input_columns = set(str(column) for column in list((schema_profile or {}).get("columns") or []) if str(column))
    planned_input_columns = set(
        str(column)
        for column in list((contract_reference or {}).get("input_columns") or [])
        if str(column)
    )
    if not uploaded_input_columns and not planned_input_columns:
        return
    planned_output_columns = set(
        str(column)
        for column in list((contract_reference or {}).get("output_columns") or [])
        if str(column)
    )
    contract_input_columns = set(contract.input_columns)
    contract_output_columns = set(contract.output_columns)
    known_source_columns = (
        uploaded_input_columns
        | planned_input_columns
        | contract_input_columns
        | planned_output_columns
        | contract_output_columns
    )

    issues: list[dict[str, Any]] = []

    if uploaded_input_columns:
        unknown_input_columns = sorted(contract_input_columns - uploaded_input_columns)
        if unknown_input_columns:
            issues.append(
                {
                    "path": "input_columns",
                    "type": "unknown_input_columns",
                    "unknown_columns": unknown_input_columns,
                    "uploaded_input_columns": sorted(uploaded_input_columns),
                    "message": (
                        "input_columns references raw source columns that are not present "
                        "in the uploaded input schema: "
                        + ", ".join(unknown_input_columns)
                        + ". Preserve the uploaded input column universe instead of inventing new raw columns."
                    ),
                }
            )

    if planned_input_columns:
        added_columns = sorted(contract_input_columns - planned_input_columns)
        removed_columns = sorted(planned_input_columns - contract_input_columns)
        if added_columns or removed_columns:
            issues.append(
                {
                    "path": "input_columns",
                    "type": "review_input_column_drift",
                    "planned_input_columns": sorted(planned_input_columns),
                    "reviewed_input_columns": sorted(contract_input_columns),
                    "added_columns": added_columns,
                    "removed_columns": removed_columns,
                    "message": (
                        "Reviewed contract input_columns drifted from the schema-valid planned "
                        "contract. Review must preserve the planned input column universe unless "
                        "repairing a specific schema-grounding defect."
                    ),
                }
            )

    def _add_unknown_reference_issue(
        *,
        path: str,
        reference_kind: str,
        unknown_columns: list[str],
    ) -> None:
        if not unknown_columns:
            return
        issues.append(
            {
                "path": path,
                "type": "unknown_column_reference",
                "reference_kind": reference_kind,
                "unknown_columns": unknown_columns,
                "uploaded_input_columns": sorted(uploaded_input_columns),
                "planned_input_columns": sorted(planned_input_columns),
                "reviewed_input_columns": sorted(contract_input_columns),
                "message": (
                    f"{path} references columns outside the uploaded/planned contract universe: "
                    + ", ".join(unknown_columns)
                    + ". Preserve canonical schema shape and repair only the invalid references."
                ),
            }
        )

    formula_input_unknown = sorted(
        column
        for column in contract.formula_input_columns
        if column not in known_source_columns
    )
    _add_unknown_reference_issue(
        path="formula_input_columns",
        reference_kind="formula_input_columns",
        unknown_columns=formula_input_unknown,
    )

    summary_group_unknown = sorted(
        column for column in contract.summary_group_keys if column not in known_source_columns
    )
    _add_unknown_reference_issue(
        path="summary_group_keys",
        reference_kind="summary_group_keys",
        unknown_columns=summary_group_unknown,
    )

    for index, metric in enumerate(contract.summary_metrics):
        if isinstance(metric, str):
            continue
        unknown_metric_columns: set[str] = set()
        if metric.source_column and metric.source_column not in known_source_columns:
            unknown_metric_columns.add(metric.source_column)
        for column in metric.group_by:
            if column not in known_source_columns:
                unknown_metric_columns.add(column)
        for column in metric.input_columns:
            if column not in known_source_columns:
                unknown_metric_columns.add(column)
        _add_unknown_reference_issue(
            path=f"summary_metrics[{index}]",
            reference_kind="summary_metric",
            unknown_columns=sorted(unknown_metric_columns),
        )

    for index, check in enumerate(contract.validation_checks):
        unknown_check_columns = sorted(
            column for column in check.input_columns if column not in known_source_columns
        )
        _add_unknown_reference_issue(
            path=f"validation_checks[{index}].input_columns",
            reference_kind="validation_check",
            unknown_columns=unknown_check_columns,
        )

    if issues:
        raise ContractSemanticValidationError(issues)


class ContractSemanticValidationError(ValueError):
    """Structured contract consistency errors that should trigger schema repair."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        message = "; ".join(str(issue.get("message") or "") for issue in issues)
        super().__init__(message)


def _directly_forbidden_formula_literals(formula: str, column: str) -> set[str]:
    """Return string literals directly forbidden by simple `column != value` checks."""
    try:
        parsed = ast.parse(formula, mode="eval")
    except SyntaxError:
        return set()

    forbidden: set[str] = set()
    for node in ast.walk(parsed):
        if not isinstance(node, ast.Compare):
            continue
        if len(node.ops) != 1 or not isinstance(node.ops[0], ast.NotEq):
            continue
        if not node.comparators:
            continue
        left = node.left
        right = node.comparators[0]
        if (
            isinstance(left, ast.Name)
            and left.id == column
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
        ):
            forbidden.add(right.value)
        if (
            isinstance(right, ast.Name)
            and right.id == column
            and isinstance(left, ast.Constant)
            and isinstance(left.value, str)
        ):
            forbidden.add(left.value)
    return forbidden


def _explicit_fallback_value(text: str) -> str | None:
    value = text.strip()
    if not value:
        return None
    if any(char.isspace() for char in value):
        return None
    if len(value) > 80:
        return None
    return value


def _validate_validation_check_consistency(contract: AuthorOutputContract) -> None:
    semantics = contract.output_column_semantics_by_name()
    issues: list[dict[str, Any]] = []
    for index, check in enumerate(contract.validation_checks):
        if not check.required or check.check_type != "formula" or not check.formula:
            continue
        columns = set(check.input_columns)
        if check.output_column:
            columns.add(check.output_column)
        for column in sorted(columns):
            forbidden = _directly_forbidden_formula_literals(check.formula, column)
            if not forbidden:
                continue
            allowed = set(contract.allowed_enums.get(column) or [])
            spec = semantics.get(column)
            fallback = (
                _explicit_fallback_value(spec.fallback_value_semantics)
                if spec is not None
                else None
            )
            protected_values = set(allowed)
            if fallback:
                protected_values.add(fallback)
            contradicted = sorted(value for value in forbidden if value in protected_values)
            for value in contradicted:
                source = (
                    "allowed_enums"
                    if value in allowed
                    else "output_column_semantics.fallback_value_semantics"
                )
                issues.append(
                    {
                        "path": f"validation_checks[{index}].formula",
                        "type": "contract_consistency",
                        "column": column,
                        "forbidden_value": value,
                        "source": source,
                        "message": (
                            f"validation_checks[{index}].formula forbids {column}={value!r}, "
                            f"but {source} declares that value valid. Hard validation checks "
                            "must not forbid explicitly allowed or fallback values; rewrite "
                            "the check as allowed-value membership, make the concern a "
                            "summary/reporting metric, or remove it."
                        ),
                    }
                )
    if issues:
        raise ContractSemanticValidationError(issues)


_EXPLICIT_CATEGORY_LIST_TRIGGER_RE = re.compile(
    r"(?:assign\s+each\s+.+?\s+to(?:\s+exactly)?\s+one\s+of|classify\s+(?:each\s+.+?\s+)?into|standard\s+categories?)\s*:\s*(?P<tail>.*)$",
    re.I,
)
_EXPLICIT_CATEGORY_LIST_STOP_RE = re.compile(
    r"^(?:use these|important|produce|include|return|only use|uploaded sample files|validation evidence|quality rules)\b",
    re.I,
)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _parse_explicit_label_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []
    if len(lines) == 1:
        body = re.sub(r",?\s+and\s+", ", ", lines[0].strip(), flags=re.I)
        parts = [part.strip() for part in body.split(",")]
    else:
        parts = []
        for line in lines:
            cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            parts.append(cleaned)
    labels = [
        part.strip(" \t\r\n.;:")
        for part in parts
        if part.strip(" \t\r\n.;:")
    ]
    return _dedupe_preserving_order(labels)


def _extract_explicit_prompt_category_labels(user_description: str) -> list[str]:
    if not user_description.strip():
        return []
    lines = user_description.splitlines()
    for index, raw_line in enumerate(lines):
        match = _EXPLICIT_CATEGORY_LIST_TRIGGER_RE.search(raw_line.strip())
        if not match:
            continue
        block_lines: list[str] = []
        inline_tail = (match.group("tail") or "").strip()
        if inline_tail:
            block_lines.append(inline_tail)
        cursor = index + 1
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if not stripped:
                break
            if _EXPLICIT_CATEGORY_LIST_STOP_RE.match(stripped):
                break
            if stripped.endswith(":") and block_lines:
                break
            block_lines.append(stripped)
            cursor += 1
        labels = _parse_explicit_label_lines(block_lines)
        if len(labels) >= 2:
            return labels
    return []


_USER_PROMPT_SEPARATE_EXCEPTION_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bseparate\s+exceptions?\s+files?\b", re.I),
    re.compile(r"\bproduce\s+(?:a\s+)?separate\s+exceptions?\s+files?\b", re.I),
    re.compile(r"\bexport\s+(?:a\s+)?(?:separate\s+)?exceptions?\s+files?\b", re.I),
)

_USER_PROMPT_SUMMARY_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsummar(?:ise|ize)\b", re.I),
    re.compile(r"\bproduce\s+(?:a\s+)?(?:separate\s+)?summary\b", re.I),
    re.compile(r"\bexport\s+(?:a\s+)?summary\b", re.I),
)


def _normalize_user_prompt_text(user_description: str) -> str:
    return " ".join(user_description.lower().split())


def _user_prompt_explicitly_requests_separate_exception_file(user_description: str) -> bool:
    normalized = _normalize_user_prompt_text(user_description)
    return any(pattern.search(normalized) for pattern in _USER_PROMPT_SEPARATE_EXCEPTION_FILE_PATTERNS)


def _user_prompt_explicitly_requests_summary_artifact(user_description: str) -> bool:
    normalized = _normalize_user_prompt_text(user_description)
    return any(pattern.search(normalized) for pattern in _USER_PROMPT_SUMMARY_ARTIFACT_PATTERNS)


def _output_file_paths_from_payload_list(entries: Any) -> set[str]:
    paths: set[str] = set()
    if not isinstance(entries, list):
        return paths
    for entry in entries:
        if isinstance(entry, str):
            path = entry
        elif isinstance(entry, dict):
            path = entry.get("path") or entry.get("output_path")
        else:
            continue
        if isinstance(path, str) and path.startswith(("outputs/", "reports/")):
            paths.add(path)
    return paths


def _summary_csv_paths_from_payload(payload: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for path in _output_file_paths_from_payload_list(payload.get("summary_output_files")):
        if not path.lower().endswith(".md"):
            paths.add(path)
    return paths


def _exception_csv_paths_from_payload(payload: dict[str, Any]) -> set[str]:
    return _output_file_paths_from_payload_list(payload.get("exception_output_files"))


def _requested_deliverables_guidance() -> dict[str, Any]:
    return {
        "planning_rules": [
            "Only include required artifacts in requested_deliverables when the user "
            "explicitly requested them or the workflow contract requires them.",
            "Backend-managed orchestration artifacts are never agent-produced even if "
            "the user prompt lists them. Do not mark these as required deliverables: "
            "events.jsonl, manifest.json, SESSION_README.md, archive.zip, "
            "reports/model_authoring_summary.md, generated/author_output_contract.json, "
            "generated/model_responses/, generated/debug/. The backend creates them "
            "after agent execution.",
            "Separate exception CSV files under outputs/ may be required when the user "
            "prompt explicitly asks for a separate exceptions file; set "
            "source='user_explicit'.",
            "Summary CSV or JSON files under outputs/ may be required when the user "
            "prompt explicitly asks to produce, export, or summarise a separate summary "
            "artifact; set source='user_explicit'.",
            "source='user_explicit' must mean the artifact is grounded in the user "
            "prompt, not merely convenient for the agent.",
            "Do not mark internally convenient summary or exception artifacts as "
            "required unless the user requested them.",
        ],
        "repair_rule": (
            "When validation rejects required summary or exception deliverables, set "
            "source='user_explicit' only if user_description explicitly requests that "
            "artifact category and output_path matches a contract-declared "
            "summary_output_files or exception_output_files path."
        ),
    }


def _sanitize_requested_deliverables_explicit_source_payload(
    payload: dict[str, Any],
    *,
    user_description: str,
) -> dict[str, Any]:
    """Mark prompt-grounded summary/exception deliverables as user_explicit before validation."""
    entries = payload.get("requested_deliverables")
    if not isinstance(entries, list) or not user_description.strip():
        return payload

    summary_paths = _summary_csv_paths_from_payload(payload)
    exception_paths = _exception_csv_paths_from_payload(payload)
    if not summary_paths and not exception_paths:
        return payload

    requests_summary = _user_prompt_explicitly_requests_summary_artifact(user_description)
    requests_exception = _user_prompt_explicitly_requests_separate_exception_file(
        user_description
    )
    if not requests_summary and not requests_exception:
        return payload

    changed = False
    sanitized_entries: list[Any] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not entry.get("required")
            or entry.get("source") == "user_explicit"
        ):
            sanitized_entries.append(entry)
            continue
        path = entry.get("output_path")
        if not isinstance(path, str):
            sanitized_entries.append(entry)
            continue
        normalized = dict(entry)
        if path in exception_paths and requests_exception:
            normalized["source"] = "user_explicit"
            changed = True
        elif path in summary_paths and requests_summary:
            normalized["source"] = "user_explicit"
            changed = True
        sanitized_entries.append(normalized)

    if not changed:
        return payload
    data = dict(payload)
    data["requested_deliverables"] = sanitized_entries
    return data


def _prompt_explicit_category_target_column(payload: dict[str, Any]) -> str | None:
    candidate_columns: list[str] = []
    for column in list(payload.get("output_columns") or []):
        text = str(column).strip()
        if text == "category" or text.endswith("_category"):
            candidate_columns.append(text)
    for column in list((payload.get("allowed_enums") or {}).keys()):
        text = str(column).strip()
        if text == "category" or text.endswith("_category"):
            candidate_columns.append(text)
    unique = _dedupe_preserving_order(candidate_columns)
    if "category" in unique:
        return "category"
    return unique[0] if len(unique) == 1 else None


def _extract_formula_enum_membership_values(
    formula: str,
    *,
    column: str,
) -> list[str] | None:
    try:
        parsed = ast.parse(formula, mode="eval")
    except SyntaxError:
        return None
    body = parsed.body
    if not isinstance(body, ast.Compare):
        return None
    if len(body.ops) != 1 or not isinstance(body.ops[0], ast.In):
        return None
    if not isinstance(body.left, ast.Name) or body.left.id != column:
        return None
    if not body.comparators:
        return None
    comparator = body.comparators[0]
    if not isinstance(comparator, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for element in comparator.elts:
        if not isinstance(element, ast.Constant):
            return None
        values.append(str(element.value))
    return values


def _format_formula_enum_membership(
    *,
    column: str,
    labels: list[str],
) -> str:
    rendered = ", ".join(repr(label) for label in labels)
    return f"{column} in [{rendered}]"


def _sanitize_prompt_explicit_category_enum_payload(
    payload: dict[str, Any],
    *,
    user_description: str,
) -> dict[str, Any]:
    labels = _extract_explicit_prompt_category_labels(user_description)
    if len(labels) < 2:
        return payload
    target_column = _prompt_explicit_category_target_column(payload)
    if not target_column:
        return payload

    data = dict(payload)
    allowed_enums = dict(data.get("allowed_enums") or {})
    changed = False
    if allowed_enums.get(target_column) != labels:
        allowed_enums[target_column] = list(labels)
        data["allowed_enums"] = allowed_enums
        changed = True

    checks = list(data.get("validation_checks") or [])
    rewritten_checks: list[Any] = []
    for item in checks:
        if not isinstance(item, dict):
            rewritten_checks.append(item)
            continue
        check = dict(item)
        formula = str(check.get("formula") or "").strip()
        if formula:
            values = _extract_formula_enum_membership_values(
                formula,
                column=target_column,
            )
            if values is not None and values != labels:
                check["formula"] = _format_formula_enum_membership(
                    column=target_column,
                    labels=labels,
                )
                changed = True
        rewritten_checks.append(check)
    if changed:
        data["validation_checks"] = rewritten_checks
    return data


def _derived_report_content_csv_paths(contract: AuthorOutputContract) -> set[str]:
    """Summary/exception CSV paths that must not be required without user_explicit source."""
    paths: set[str] = set()
    for entry in contract.summary_output_files:
        path = output_file_path(entry)
        if path and not path.lower().endswith(".md"):
            paths.add(path)
    for entry in contract.exception_output_files:
        path = output_file_path(entry)
        if path:
            paths.add(path)
    return paths


def _deliverable_gate_path(entry: str | DeliverableSpec) -> str | None:
    if isinstance(entry, str):
        return entry
    return entry.output_path or None


def _validate_required_deliverable_discipline(contract: AuthorOutputContract) -> None:
    """Reject required deliverables for report-derived CSVs unless user-explicit."""
    derived_csvs = _derived_report_content_csv_paths(contract)
    issues: list[dict[str, Any]] = []
    for index, entry in enumerate(contract.requested_deliverables):
        if not deliverable_required(entry):
            continue
        gate_path = _deliverable_gate_path(entry)
        if gate_path:
            normalized = _normalize_agent_path_literal(gate_path)
            if normalized in _BACKEND_MANAGED_ARTIFACT_PATHS or any(
                normalized.startswith(prefix)
                for prefix in _GENERATED_AGENT_FORBIDDEN_WRITE_PREFIXES
            ):
                issues.append(
                    {
                        "path": f"requested_deliverables[{index}]",
                        "type": "contract_requiredness",
                        "output_path": gate_path,
                        "forbidden_field": "required",
                        "message": (
                            f"requested_deliverables[{index}] marks {gate_path!r} as required, "
                            "but that path is backend-managed orchestration metadata. "
                            "Generated agents must not write events.jsonl, manifest.json, "
                            "SESSION_README.md, archive.zip, model responses, or other "
                            "backend audit/control files. Remove it from required "
                            "requested_deliverables or set required=false."
                        ),
                    }
                )
                continue
        path = deliverable_output_path(entry)
        if not path or path not in derived_csvs:
            continue
        source = entry.source if isinstance(entry, DeliverableSpec) else None
        if source != "user_explicit":
            issues.append(
                {
                    "path": f"requested_deliverables[{index}]",
                    "type": "contract_requiredness",
                    "output_path": path,
                    "forbidden_field": "required",
                    "message": (
                        f"requested_deliverables[{index}] marks {path!r} as required, "
                        "but that path is a summary or exception CSV derived from report "
                        "content. Keep category counts, uncertain rows, and human-review "
                        "rows inside the validation report unless the user explicitly "
                        "requested a separate file. Remove it from required "
                        "requested_deliverables, set required=false, or set "
                        "source='user_explicit' only when the user prompt explicitly "
                        "asks for that separate artifact."
                    ),
                }
            )
    if issues:
        raise ContractSemanticValidationError(issues)


def _validate_prompt_explicit_category_enum_faithfulness(
    contract: AuthorOutputContract,
    *,
    user_description: str,
) -> None:
    prompt_labels = _extract_explicit_prompt_category_labels(user_description)
    if len(prompt_labels) < 2:
        return
    target_column = None
    if "category" in contract.output_columns or "category" in contract.allowed_enums:
        target_column = "category"
    else:
        category_columns = [
            column
            for column in contract.output_columns
            if column.endswith("_category")
        ]
        if len(category_columns) == 1:
            target_column = category_columns[0]
    if not target_column:
        return

    issues: list[dict[str, Any]] = []
    emitted_labels = list(contract.allowed_enums.get(target_column) or [])
    if emitted_labels != prompt_labels:
        issues.append(
            {
                "path": f"allowed_enums.{target_column}",
                "type": "prompt_explicit_allowed_enums_mismatch",
                "column": target_column,
                "prompt_category_labels": prompt_labels,
                "emitted_category_labels": emitted_labels,
                "message": (
                    f"allowed_enums.{target_column} must preserve the explicit category "
                    "labels from the user prompt exactly. Do not replace prompt-provided "
                    "labels with inferred alternative labels."
                ),
            }
        )

    for index, check in enumerate(contract.validation_checks):
        formula = str(check.formula or "").strip()
        if not formula:
            continue
        values = _extract_formula_enum_membership_values(formula, column=target_column)
        if values is None or values == prompt_labels:
            continue
        issues.append(
            {
                "path": f"validation_checks[{index}].formula",
                "type": "prompt_explicit_validation_enum_mismatch",
                "column": target_column,
                "prompt_category_labels": prompt_labels,
                "emitted_category_labels": values,
                "message": (
                    f"validation_checks[{index}].formula uses category labels that do "
                    "not match the explicit user-prompt category list. Preserve the "
                    "prompt-provided labels exactly in both allowed_enums and any "
                    "required category-membership validation check."
                ),
            }
        )
    if issues:
        raise ContractSemanticValidationError(issues)


def _validate_contract_semantics(
    contract: AuthorOutputContract,
    *,
    schema_profile: dict[str, Any] | None = None,
    contract_reference: dict[str, Any] | None = None,
    user_description: str = "",
) -> None:
    _validate_required_output_column_semantics(contract)
    _validate_calculated_field_formulas(contract, schema_profile=schema_profile)
    _validate_input_column_faithfulness(
        contract,
        schema_profile=schema_profile,
        contract_reference=contract_reference,
    )
    _validate_validation_check_consistency(contract)
    _validate_prompt_explicit_category_enum_faithfulness(
        contract,
        user_description=user_description,
    )
    _validate_required_deliverable_discipline(contract)


def _validate_contract_payload_strict(
    payload: dict[str, Any],
    *,
    schema_profile: dict[str, Any] | None = None,
    contract_reference: dict[str, Any] | None = None,
    user_description: str = "",
) -> AuthorOutputContract:
    contract = AuthorOutputContract.model_validate(payload)
    _validate_contract_paths(contract)
    _validate_contract_semantics(
        contract,
        schema_profile=schema_profile,
        contract_reference=contract_reference,
        user_description=user_description,
    )
    return contract


def _allowed_schema_types() -> dict[str, list[str]]:
    return {
        "CalculatedFieldSpec": [
            "name",
            "description",
            "formula",
        ],
        "OutputFileSpec": [
            "path",
            "description",
            "required_columns",
            "optional_columns",
        ],
        "OutputColumnSemanticsSpec": [
            "name",
            "description",
            "producer_kind",
            "required",
            "nullable",
            "allow_empty_string",
            "row_semantics",
            "fallback_value_semantics",
        ],
        "ExceptionRuleSpec": [
            "name",
            "condition",
            "reason",
            "severity",
            "output_column",
        ],
        "ValidationCheckSpec": [
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
        ],
        "SummaryMetricSpec": [
            "name",
            "metric_type",
            "source_column",
            "group_by",
            "filter",
            "description",
            "formula",
            "input_columns",
            "output_column",
        ],
        "DeliverableSpec": [
            "name",
            "description",
            "output_path",
            "required",
            "status",
            "source",
        ],
    }


_CONTRACT_FORBIDDEN_TOP_LEVEL_FLAGS: frozenset[str] = frozenset(
    {"preserve_original_data", "enable_logging", "enable_debugging"}
)

_CONTRACT_FORBIDDEN_TOP_LEVEL_META_FIELDS: frozenset[str] = frozenset(
    {
        "input_schema_path",
        "output_schema_path",
        "output_data_formats",
        "input_file_pattern",
        "output_file_prefix",
        "required_file_profile_fields",
        "calculated_field_guidance",
        "exception_rule_guidance",
        "validation_check_guidance",
        "allowed_enums_guidance",
        "artifact_path_namespace_guidance",
        "output_column_semantics_guidance",
        "summary_metric_guidance",
        "required_output_semantics_guidance",
        "formula_columns",
    }
)


def _slugify_identifier(text: str, *, fallback: str = "generated_value") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or fallback


def _summary_metric_examples() -> list[dict[str, Any]]:
    return [
        {
            "name": "category_counts",
            "metric_type": "count",
            "source_column": "category",
            "group_by": ["category"],
            "description": "Count transactions by category",
            "input_columns": ["category"],
            "output_column": "category_count",
        },
        {
            "name": "uncertain_rows_count",
            "metric_type": "count",
            "source_column": "confidence",
            "filter": "confidence < 0.8",
            "description": "Count rows with low confidence",
            "input_columns": ["confidence"],
            "output_column": "uncertain_rows_count",
        },
    ]


def _summary_metric_guidance() -> dict[str, Any]:
    return {
        "allowed_fields": _allowed_schema_types()["SummaryMetricSpec"],
        "forbidden_fields": ["condition"],
        "valid_examples": _summary_metric_examples(),
        "repair_rule": (
            "If a metric uses forbidden fields such as `condition`, convert it to the "
            "allowed `filter` or `formula` representation when possible; otherwise "
            "remove or simplify that metric so the contract remains valid."
        ),
    }


_VALIDATION_CHECK_FORBIDDEN_NESTED_FIELDS: frozenset[str] = frozenset(
    {
        "allowed_enums",
        "allowed_values",
        "categories",
        "min_value",
        "max_value",
        "minimum",
        "maximum",
        "enum_values",
        "range",
        "bounds",
        "thresholds",
    }
)


def _calculated_field_guidance() -> dict[str, Any]:
    return {
        "allowed_fields": _allowed_schema_types()["CalculatedFieldSpec"],
        "required_fields": ["name", "description"],
        "valid_examples": [
            {
                "name": "residual_balance",
                "description": "Residual balance after payments, credits, and deductions",
                "formula": "invoice_amount - paid_amount - credit_note_amount - deduction_amount",
            }
        ],
        "formula_discipline": {
            "allowed_formula_shape": _FORMULA_DSL_DESCRIPTION,
            "non_arithmetic_output_suffixes": list(_NON_ARITHMETIC_OUTPUT_SUFFIXES),
            "non_arithmetic_examples": [
                "recommended_payment_date",
                "payment_status",
                "exception_reason",
                "aging_bucket",
            ],
        },
        "repair_rule": (
            "CalculatedFieldSpec allows only name, description, and optional formula. "
            "If a calculated field is otherwise valid but description is missing, add "
            "a concise schema-level description derived only from the field name or "
            "formula without inventing new business logic. Only keep formula for "
            "numeric arithmetic derived outputs. If the field is clearly a date, "
            "status, reason, bucket/category, flag, ready/required boolean, or "
            "confidence-style derived output, remove formula and express the field "
            "through output_column_semantics instead."
        ),
    }


def _exception_rule_guidance() -> dict[str, Any]:
    return {
        "allowed_fields": _allowed_schema_types()["ExceptionRuleSpec"],
        "forbidden_fields": ["issue_flag"],
        "valid_examples": [
            {
                "name": "requires_review",
                "condition": "residual_balance > 0",
                "reason": "Residual balance remains open after remittance application.",
                "severity": "warning",
                "output_column": "issue_flag",
            }
        ],
        "repair_rule": (
            "ExceptionRuleSpec allows only name, condition, reason, severity, and "
            "optional output_column. Do not emit issue_flag as a nested field. If an "
            "invalid issue_flag field appears, treat the field name as the intended "
            "output_column and move any human-readable text value into reason when that "
            "mapping is unambiguous."
        ),
    }


def _validation_check_examples() -> dict[str, Any]:
    return {
        "top_level_allowed_enums": {
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
        "valid_formula_range_check": {
            "check_id": "confidence_range",
            "layer": "business_rules",
            "name": "Confidence score between 0 and 1",
            "required": True,
            "check_type": "formula",
            "output_column": "confidence",
            "input_columns": ["confidence"],
            "formula": "0 <= confidence <= 1",
            "severity": "error",
        },
    }


def _validation_check_guidance() -> dict[str, Any]:
    allowed = _allowed_schema_types()["ValidationCheckSpec"]
    return {
        "allowed_fields": allowed,
        "forbidden_nested_fields": sorted(_VALIDATION_CHECK_FORBIDDEN_NESTED_FIELDS),
        "valid_examples": _validation_check_examples(),
        "planning_rules": [
            "ValidationCheckSpec allows only the allowed_fields listed above.",
            "Every validation check must include check_id, layer, name, and required.",
            "Never put allowed_enums, allowed_values, categories, min_value, max_value, "
            "minimum, maximum, enum_values, range, bounds, or thresholds inside validation_checks.",
            "Category membership belongs in top-level allowed_enums only — do not "
            "duplicate enum lists inside validation_checks.",
            "Numeric range constraints must not use min_value or max_value. Use a "
            "formula-based validation_checks entry with check_type formula when a "
            "hard range gate is needed; otherwise omit the unsupported range check.",
        ],
        "repair_rule": (
            "If validation_checks[*].allowed_enums is forbidden, remove it from that "
            "check and preserve the enum list in top-level allowed_enums. Do not "
            "duplicate enum values inside validation_checks. If validation_checks[*] "
            "uses allowed_values, categories, or enum_values to express allowed labels, "
            "move those values into top-level allowed_enums for the target column when "
            "the target column is unambiguous, then remove the invalid nested field and "
            "drop the redundant check. If "
            "validation_checks[*].min_value or max_value is forbidden, remove those "
            "keys and convert to a formula-based check using only allowed "
            "ValidationCheckSpec fields when possible; otherwise remove the invalid "
            "range check. If check_id is missing but name is present, derive a concise "
            "snake_case check_id from name. If layer is missing, infer a conservative "
            "layer value such as row_level or business_rules from check_type/output_file. "
            "Never return the same forbidden nested keys."
        ),
    }


def _output_column_semantics_examples() -> dict[str, Any]:
    return {
        "required_passthrough_input_column": {
            "name": "invoice_id",
            "description": "Source row identifier preserved in the clean row-level output",
            "producer_kind": "copied_input",
            "required": True,
            "nullable": False,
            "allow_empty_string": False,
            "row_semantics": (
                "Copied unchanged from the source row into the clean row-level output "
                "to preserve traceability."
            ),
            "fallback_value_semantics": (
                "Carry the normalized source-row value through unchanged and do not "
                "invent a new derived business value for this copied input field."
            ),
        },
        "required_derived_output_column": {
            "name": "residual_balance",
            "description": "Residual balance remaining after payments, credits, and deductions",
            "producer_kind": "calculation",
            "required": True,
            "nullable": False,
            "allow_empty_string": False,
            "row_semantics": (
                "Emit the derived residual balance for each output row using the "
                "contract's calculation or documented row-level logic."
            ),
            "fallback_value_semantics": (
                "Use the workflow's explicit default or normalization rule so the "
                "derived value is never left blank."
            ),
        },
    }


def _output_column_semantics_guidance() -> dict[str, Any]:
    return {
        "allowed_fields": _allowed_schema_types()["OutputColumnSemanticsSpec"],
        "producer_kind_examples": [
            "copied_input",
            "classification",
            "rule_explanation",
            "calculation",
            "summary",
            "exception_flag",
            "other",
        ],
        "valid_examples": _output_column_semantics_examples(),
        "planning_rules": [
            "Every required_output_columns entry must have a matching OutputColumnSemanticsSpec.",
            "Do not list a column as required unless its output_column_semantics are defined.",
            "Do not omit output_column_semantics for passthrough columns merely because they already exist in input_columns.",
            "If a required output column is copied unchanged from input_columns, use producer_kind copied_input and describe it as copied unchanged for traceability.",
            "If a required output column is derived and not present in input_columns, do not mark it as copied_input. Explain how the value is calculated, assigned, bucketed, flagged, or otherwise produced.",
            "Only place passthrough input columns in required_output_columns when the workflow truly requires them as non-null output fields; otherwise keep them in output_columns for traceability only.",
        ],
    }


def _required_output_semantics_guidance(
    *,
    payload: dict[str, Any],
    schema_profile: dict[str, Any],
) -> dict[str, Any]:
    input_columns = list(
        dict.fromkeys(
            list(payload.get("input_columns") or [])
            + list(schema_profile.get("columns") or [])
        )
    )
    required_output_columns = list(payload.get("required_output_columns") or [])
    existing_semantics = {
        str(item.get("name"))
        for item in list(payload.get("output_column_semantics") or [])
        if isinstance(item, dict) and item.get("name")
    }
    missing_required_columns = [
        column for column in required_output_columns if column not in existing_semantics
    ]
    passthrough_missing = [
        column for column in missing_required_columns if column in set(input_columns)
    ]
    calculated_fields: dict[str, dict[str, Any]] = {}
    for item in list(payload.get("calculated_fields") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        calculated_fields[name] = {
            "description": str(item.get("description") or "").strip(),
            "formula": str(item.get("formula") or "").strip(),
        }
    exception_outputs = {
        str(item.get("output_column") or "").strip()
        for item in list(payload.get("exception_rules") or [])
        if isinstance(item, dict) and str(item.get("output_column") or "").strip()
    }
    derived_hints: list[dict[str, Any]] = []
    for column in missing_required_columns:
        if column in passthrough_missing:
            continue
        hint: dict[str, Any] = {"name": column}
        calculated = calculated_fields.get(column)
        if calculated is not None:
            hint["classification"] = "derived_calculation"
            hint["calculated_field"] = calculated
        elif column in exception_outputs:
            hint["classification"] = "derived_exception_flag"
            hint["repair_expectation"] = (
                "Define a non-empty exception or review flag meaning for this derived "
                "output column."
            )
        else:
            hint["classification"] = "derived_required_output"
            hint["repair_expectation"] = (
                "This required output column is not present in input_columns, so do not "
                "treat it as copied_input. Use calculated_fields, exception_rules, "
                "classification_rules, summary context, and user_description to define "
                "its row-level meaning."
            )
        derived_hints.append(hint)
    passthrough_template = dict(
        _output_column_semantics_examples()["required_passthrough_input_column"]
    )
    passthrough_template["name"] = "<required passthrough column name>"
    return {
        "input_columns": input_columns,
        "required_output_columns": required_output_columns,
        "existing_output_column_semantics": sorted(existing_semantics),
        "missing_required_output_columns": missing_required_columns,
        "missing_passthrough_required_columns": passthrough_missing,
        "missing_derived_required_columns": [hint["name"] for hint in derived_hints],
        "passthrough_semantics_template": passthrough_template,
        "derived_column_hints": derived_hints,
        "repair_rule": (
            "For every name in missing_passthrough_required_columns, add an "
            "OutputColumnSemanticsSpec using passthrough_semantics_template with "
            "that column name. For every name in missing_derived_required_columns, "
            "add explicit derived semantics and do not mark the column as copied_input."
        ),
    }


def _infer_validation_check_target_column(
    check: dict[str, Any],
    *,
    known_columns: set[str],
) -> str | None:
    output_column = check.get("output_column")
    if isinstance(output_column, str) and output_column.strip():
        return output_column
    input_columns = [
        str(column).strip()
        for column in list(check.get("input_columns") or [])
        if str(column).strip()
    ]
    if len(input_columns) == 1:
        return input_columns[0]
    if input_columns:
        unique = list(dict.fromkeys(input_columns))
        if len(unique) == 1:
            return unique[0]
    text = _slugify_identifier(
        " ".join(str(check.get(key) or "") for key in ("check_id", "name")),
        fallback="",
    )
    matches = sorted(
        (
            column
            for column in known_columns
            if _slugify_identifier(column, fallback="") in text
        ),
        key=len,
        reverse=True,
    )
    if not matches:
        return None
    top = matches[0]
    if len(matches) > 1 and len(matches[1]) == len(top):
        return None
    return top


def _infer_validation_check_layer(check: dict[str, Any]) -> str | None:
    check_type = str(check.get("check_type") or "").strip().lower()
    if check.get("output_file"):
        return "artifacts"
    if check_type in {"row_count", "presence"}:
        return "row_level"
    if check_type in {"formula", "allowed_value", "enum"} or check.get("formula"):
        return "business_rules"
    return None


def _sanitize_validation_checks_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Move or strip unsupported validation_checks fields before strict validation."""
    data = dict(payload)
    top_enums = dict(data.get("allowed_enums") or {})
    known_columns = {
        str(column)
        for column in list(data.get("input_columns") or []) + list(data.get("output_columns") or [])
        if str(column).strip()
    }
    sanitized_checks: list[Any] = []
    for item in list(data.get("validation_checks") or []):
        if not isinstance(item, dict):
            sanitized_checks.append(item)
            continue
        check = dict(item)
        nested_enums = check.pop("allowed_enums", None)
        raw_allowed_values = check.get("allowed_values")
        raw_categories = check.get("categories")
        raw_enum_values = check.get("enum_values")
        inferred_column = _infer_validation_check_target_column(check, known_columns=known_columns)
        if isinstance(nested_enums, dict):
            for column, values in nested_enums.items():
                if column not in top_enums or not top_enums.get(column):
                    top_enums[column] = list(values)
        enum_like_values = None
        for candidate in (raw_allowed_values, raw_categories, raw_enum_values):
            if isinstance(candidate, list) and candidate:
                enum_like_values = list(candidate)
                break
        handled_enum_like = False
        if enum_like_values is not None and inferred_column:
            if inferred_column not in top_enums or not top_enums.get(inferred_column):
                top_enums[inferred_column] = enum_like_values
            handled_enum_like = True
        min_val = check.pop("min_value", None)
        max_val = check.pop("max_value", None)
        for forbidden in _VALIDATION_CHECK_FORBIDDEN_NESTED_FIELDS:
            if forbidden in {"allowed_values", "categories", "enum_values"} and not handled_enum_like:
                continue
            check.pop(forbidden, None)
        drop_check = False
        if nested_enums is not None or check.get("check_type") == "enum":
            drop_check = True
        if handled_enum_like:
            drop_check = True
        column = inferred_column
        if min_val is not None or max_val is not None or check.get("check_type") == "range":
            if column is not None and (min_val is not None or max_val is not None):
                parts: list[str] = []
                if min_val is not None:
                    parts.append(f"{min_val} <= {column}")
                if max_val is not None:
                    parts.append(f"{column} <= {max_val}")
                check["formula"] = (
                    " and ".join(parts) if len(parts) > 1 else parts[0]
                )
                check["check_type"] = "formula"
                if column and column not in (check.get("input_columns") or []):
                    check["input_columns"] = list(check.get("input_columns") or []) + [
                        column
                    ]
            else:
                drop_check = True
        if not check.get("check_id"):
            name = str(check.get("name") or "").strip()
            if name:
                check["check_id"] = _slugify_identifier(name, fallback="validation_check")
        if not check.get("layer"):
            inferred_layer = _infer_validation_check_layer(check)
            if inferred_layer:
                check["layer"] = inferred_layer
        if drop_check:
            continue
        sanitized_checks.append(check)
    data["allowed_enums"] = top_enums
    data["validation_checks"] = sanitized_checks
    return data


_ALLOWED_ENUMS_WRAPPER_KEYS: tuple[str, ...] = (
    "values",
    "allowed_values",
    "enum_values",
    "categories",
)


def _unwrap_allowed_enum_entry(value: Any) -> tuple[Any, bool]:
    if isinstance(value, list):
        return value, False
    if not isinstance(value, dict):
        return value, False
    list_candidates: dict[str, list[Any]] = {}
    for key in _ALLOWED_ENUMS_WRAPPER_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, list):
            list_candidates[key] = candidate
    if not list_candidates:
        return value, False
    if len(list_candidates) == 1:
        return next(iter(list_candidates.values())), True
    lists = list(list_candidates.values())
    first = lists[0]
    if all(item == first for item in lists[1:]):
        return first, True
    return value, False


def _sanitize_allowed_enums_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap intuitive but invalid allowed_enums wrapper dicts before strict validation."""
    data = dict(payload)
    raw_enums = data.get("allowed_enums")
    if not isinstance(raw_enums, dict):
        return data
    sanitized_enums: dict[str, Any] = {}
    changed = False
    for column, value in raw_enums.items():
        unwrapped, entry_changed = _unwrap_allowed_enum_entry(value)
        coerced, scalar_changed = _coerce_allowed_enum_list_to_strings(unwrapped)
        sanitized_enums[column] = coerced
        changed = changed or entry_changed or scalar_changed
    if changed:
        data["allowed_enums"] = sanitized_enums
    return data


def _coerce_allowed_enum_list_to_strings(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, list):
        return value, False
    if not value:
        return value, False
    for item in value:
        if item is None or isinstance(item, (dict, list)):
            return value, False
    if not all(isinstance(item, (str, int, float, bool)) for item in value):
        return value, False
    coerced = [item if isinstance(item, str) else str(item) for item in value]
    return coerced, coerced != value


def _known_input_columns(
    *,
    payload: dict[str, Any],
    schema_profile: dict[str, Any],
) -> set[str]:
    columns: list[str] = []
    columns.extend(str(column) for column in list(payload.get("input_columns") or []) if str(column))
    columns.extend(str(column) for column in list(schema_profile.get("columns") or []) if str(column))
    return set(dict.fromkeys(columns))


def _passthrough_output_column_semantics_spec(column: str) -> dict[str, Any]:
    spec = dict(_output_column_semantics_examples()["required_passthrough_input_column"])
    spec["name"] = column
    spec["description"] = f"Copied from input column {column} for traceability/review."
    return spec


def _sanitize_required_output_column_semantics_payload(
    payload: dict[str, Any],
    *,
    schema_profile: dict[str, Any],
) -> dict[str, Any]:
    """Add copied-input semantics for exact input-column passthrough gaps only."""
    data = dict(payload)
    required_columns = list(data.get("required_output_columns") or [])
    if not required_columns:
        return data
    input_columns = _known_input_columns(payload=data, schema_profile=schema_profile)
    semantics = list(data.get("output_column_semantics") or [])
    existing_names = {
        str(item.get("name"))
        for item in semantics
        if isinstance(item, dict) and item.get("name")
    }
    appended: list[Any] = []
    for column in required_columns:
        if column in existing_names or column not in input_columns:
            continue
        appended.append(_passthrough_output_column_semantics_spec(column))
        existing_names.add(column)
    if appended:
        data["output_column_semantics"] = semantics + appended
    return data


def _finalize_contract_payload(
    payload: dict[str, Any],
    *,
    schema_profile: dict[str, Any],
    user_description: str,
    contract_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply deterministic, unambiguous contract finalization before validation.

    This consolidates formatting/schema hygiene and field-placement discipline without
    weakening strict validation. Ambiguous cases are left for schema repair or failure.
    """

    candidate = _sanitize_contract_alternate_dialect_payload(
        payload,
        schema_profile=schema_profile,
        contract_reference=contract_reference,
    )
    candidate, _shape_changed = _sanitize_contract_shape_payload(
        payload=candidate,
        schema_profile=schema_profile,
    )
    candidate = _sanitize_validation_checks_payload(candidate)
    candidate = _sanitize_allowed_enums_payload(candidate)
    candidate = _sanitize_prompt_explicit_category_enum_payload(
        candidate,
        user_description=user_description,
    )
    candidate = _sanitize_required_output_column_semantics_payload(
        candidate,
        schema_profile=schema_profile,
    )
    candidate = _sanitize_formula_disciplined_calculated_fields_payload(
        candidate,
        schema_profile=schema_profile,
    )
    candidate = _sanitize_requested_deliverables_explicit_source_payload(
        candidate,
        user_description=user_description,
    )
    return candidate


def _allowed_enums_guidance() -> dict[str, Any]:
    return {
        "valid_shape": {
            "allowed_enums": {
                "status": ["open", "closed"],
                "risk_level": ["low", "medium", "high"],
            }
        },
        "invalid_shapes": [
            {"status": {"values": ["open", "closed"]}},
            {"status": {"allowed_values": ["open", "closed"]}},
            {"status": {"enum_values": ["open", "closed"]}},
            {"status": {"categories": ["open", "closed"]}},
        ],
        "planning_rules": [
            "allowed_enums must map output column names to plain JSON arrays.",
            "Do not wrap enum lists inside values, allowed_values, enum_values, or categories objects.",
            "Enum values must be the exact labels expected in the generated output column.",
            "Bucket columns should use actual output labels, not threshold metadata, unless the output column genuinely contains the raw threshold values.",
            "If an enum list uses simple numeric or boolean scalar labels and the schema expects strings, emit those labels as strings rather than as raw numbers or booleans.",
            "Do not nest metadata such as description, source, rationale, or type inside allowed_enums.",
        ],
        "repair_rule": (
            "For top-level allowed_enums only, unwrap dict wrappers that contain a single "
            "enum list under values, allowed_values, enum_values, or categories into the "
            "plain list form. If an allowed_enums list contains only simple scalar "
            "labels (strings, numbers, or booleans), convert non-string scalar labels "
            "to their exact string form without inventing semantic bucket names. "
            "Preserve entries that are already plain lists. Do not invent enum labels, "
            "delete allowed_enums entries, or nest metadata inside allowed_enums."
        ),
    }


def _contract_return_shape_guidance() -> dict[str, Any]:
    return {
        "forbidden_top_level_fields": sorted(
            _CONTRACT_FORBIDDEN_TOP_LEVEL_META_FIELDS | _CONTRACT_FORBIDDEN_TOP_LEVEL_FLAGS | {"output_format"}
        ),
        "planning_rules": [
            "Return a single AuthorOutputContract-shaped JSON object only.",
            "Prompt guidance belongs in the prompt only — never as top-level contract fields.",
            "Do not include schema documentation, guidance objects, file-pattern metadata, or schema paths.",
            "input_format is required and must match the uploaded input type.",
            'Use input_format \"csv\" for CSV uploads and \"xlsx\" for Excel/XLSX uploads.',
            "Do not invent new input_format values such as excel, spreadsheet, or workbook.",
        ],
        "repair_rule": (
            "Remove forbidden top-level prompt-leakage/meta fields from the contract object. "
            "Populate input_format only from known upload metadata or an unambiguous input_file "
            "extension (.csv -> csv, .xlsx -> xlsx). Do not delete real deliverables, output "
            "files, or business-logic fields."
        ),
    }


def _format_validation_error_loc(loc: tuple[Any, ...] | list[Any]) -> str:
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
            continue
        text = str(item)
        parts.append(text)
    return ".".join(part for part in parts if part)


def _normalise_validation_error_path(path: str) -> str:
    if not path:
        return path
    schema_type_names = set(_allowed_schema_types()) | {
        "str",
        "int",
        "float",
        "bool",
        "dict",
        "list",
    }
    normalized_parts = [
        part
        for part in path.split(".")
        if part and part not in schema_type_names
    ]
    return ".".join(normalized_parts) or path


def _contract_validation_issue_details(exc: ValidationError | ValueError) -> list[dict[str, Any]]:
    if isinstance(exc, ValidationError):
        issues: list[dict[str, Any]] = []
        for error in exc.errors():
            loc = error.get("loc") or ()
            path = _normalise_validation_error_path(_format_validation_error_loc(loc))
            issue: dict[str, Any] = {
                "path": path,
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            if error.get("type") == "extra_forbidden" and loc:
                issue["forbidden_field"] = str(loc[-1])
            issues.append(issue)
        return issues
    if isinstance(exc, ContractSemanticValidationError):
        return exc.issues
    return [{"path": "", "message": str(exc), "type": "value_error"}]


async def _repair_contract_schema_payload(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    responses_dir: Path,
    source_stage: str,
    payload: dict[str, Any],
    validation_error: str,
    validation_issue_details: list[dict[str, Any]],
    schema_profile: dict[str, Any],
    user_description: str,
    contract_reference: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> AuthorOutputContract | None:
    payload = _finalize_contract_payload(
        payload,
        schema_profile=schema_profile,
        user_description=user_description,
        contract_reference=contract_reference,
    )
    file_profile = _contract_file_profile_metadata(schema_profile)
    repair_prompt = _json_prompt(
        {
            "stage": "contract_schema_repair",
            "source_stage": source_stage,
            "validation_error": validation_error,
            "validation_issue_details": validation_issue_details,
            "invalid_contract_object": payload,
            "schema_profile": schema_profile,
            "user_description": user_description,
            "required_top_level_skeleton": _contract_top_level_skeleton(
                schema_profile=schema_profile,
                workflow_type=str(payload.get("workflow_type") or "custom"),
            ),
            "required_file_profile_fields": file_profile,
            "allowed_build_mode_values": list(_BUILD_MODE_VALUES),
            **_contract_stage_guidance(mode="schema_repair"),
            "contract_finalization_guidance": _contract_finalization_guidance(
                payload=payload,
                schema_profile=schema_profile,
                contract_reference=contract_reference,
            ),
            "required_output_semantics_guidance": _required_output_semantics_guidance(
                payload=payload,
                schema_profile=schema_profile,
            ),
            "uploaded_input_columns": list(schema_profile.get("columns") or []),
            "planned_contract_input_columns": list(
                (contract_reference or {}).get("input_columns") or []
            ),
            "reviewed_contract_input_columns": list(payload.get("input_columns") or []),
            "instruction": (
                "Repair the invalid AuthorOutputContract into the exact schema. "
                "Return JSON only with author_output_contract. Include every required "
                "top-level field shown in required_top_level_skeleton. Echo "
                "required_file_profile_fields exactly for input_file, input_format, "
                "and selected_sheet when present. Follow "
                "contract_return_shape_guidance: return only AuthorOutputContract fields "
                "and never top-level prompt guidance or schema-path metadata. build_mode must be one of "
                "allowed_build_mode_values — do not invent new values. Do not rename "
                "keys, do not add extra keys, preserve valid business logic, and make "
                "the smallest correction needed for the listed validation issues. Follow "
                "artifact_path_namespace_guidance.repair_rule for invalid output path "
                "namespaces such as summaries/ or summary/. Follow "
                "allowed_enums_guidance.repair_rule for allowed_enums wrapper dicts. Use "
                "only allowed_schema_types field names for structured objects — for "
                "example CalculatedFieldSpec requires name and description, "
                "ExceptionRuleSpec allows only name, condition, reason, severity, "
                "and output_column, "
                "example SummaryMetricSpec may include formula, input_columns, and "
                "output_column; DeliverableSpec requires name. SummaryMetricSpec "
                "must not contain `condition`; convert that intent into `filter` or "
                "remove/simplify the metric when necessary. Follow "
                "validation_check_guidance.repair_rule for validation_checks "
                "extra_forbidden errors: remove nested allowed_enums and keep enum "
                "semantics in top-level allowed_enums; remove min_value/max_value and "
                "convert to formula-based checks or drop unsupported range checks. "
                "Never return forbidden nested validation_checks keys. Every "
                "required_output_columns entry must have explicit output_column_semantics "
                "with non-null/blank rules and fallback semantics. Only keep a "
                "calculated_fields formula when it fits the supported arithmetic DSL; "
                "otherwise remove the unsupported formula and express that field "
                "through output_column_semantics. Apply contract_finalization_guidance: "
                "non-arithmetic derived outputs such as *_date, *_status, *_reason, "
                "*_bucket, *_category, *_flag, *_ready, and *_required must use "
                "output_column_semantics rather than formula. Use "
                "calculated_field_guidance for calculated_fields and "
                "exception_rule_guidance for exception_rules. "
                "If validation_issue_details points "
                "to missing required output semantics, follow "
                "required_output_semantics_guidance.repair_rule: add copied_input "
                "semantics for missing_passthrough_required_columns using "
                "passthrough_semantics_template, and add explicit derived semantics "
                "for missing_derived_required_columns using calculated_fields, "
                "exception_rules, and user_description. Do not treat derived columns "
                "as copied_input. If validation_issue_details points "
                "to calculated_fields[*].description missing, add a concise "
                "description derived only from the field name or formula. If "
                "validation_issue_details points to exception_rules[*].issue_flag, "
                "move that nested field into output_column='issue_flag' and reason "
                "when the mapping is unambiguous. If validation_issue_details points "
                "to requested_deliverables[*] or missing_deliverables[*] using the "
                "forbidden field `path`, rename that field to output_path, preserve "
                "required/status/source/description when present, and provide a "
                "concise deterministic DeliverableSpec.name when it is missing. "
                "Do not flatten required structured deliverables into plain strings. "
                "If validation_issue_details shows top-level output_format as "
                "extra_forbidden, remove output_format entirely. If "
                "validation_issue_details shows input_file missing, copy "
                "required_file_profile_fields.input_file exactly. If "
                "validation_issue_details only lists narrow schema-shape defects, "
                "preserve the rest of invalid_contract_object unchanged instead of "
                "rewriting the whole contract. If validation_issue_details points "
                "to validation_checks[*] missing check_id or layer, derive them "
                "conservatively from name and check_type. If "
                "validation_issue_details points to validation_checks[*].allowed_values, "
                "categories, or enum_values, move those values into top-level "
                "allowed_enums for the target column when the target column is "
                "unambiguous, then remove the redundant check. If validation_issue_details "
                "points to preserve_original_data, enable_logging, or enable_debugging, "
                "remove those forbidden top-level extras. If validation_issue_details points "
                "to validation_checks[*].formula with type contract_consistency, "
                "rewrite the hard check so it no longer forbids allowed_enums or "
                "fallback_value_semantics values; use allowed-value membership, "
                "summary_metrics/report semantics, or remove the check if it is only "
                "advisory. If validation_issue_details points to "
                "prompt_explicit_allowed_enums_mismatch or "
                "prompt_explicit_validation_enum_mismatch, preserve the explicit "
                "category labels from user_description exactly in allowed_enums for "
                "that column and rewrite any category-membership formula check to use "
                "that same exact list. If validation_issue_details points to "
                "unknown_input_columns, "
                "review_input_column_drift, or unknown_column_reference, restore "
                "uploaded_input_columns/planned_contract_input_columns and preserve "
                "the canonical planned input column universe instead of inventing a "
                "new workflow domain. "
                "Review must preserve the planned input column universe unless a "
                "specific schema-grounding defect requires a narrow correction. "
                "If prompt/file mismatch is material, "
                "set build_mode to clarification and provide clarification_questions."
            ),
        },
    )
    _record_stage_prompt_metrics(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        purpose="contract_schema_repair",
        prompt=repair_prompt,
        compact_mode=_use_compact_contract_prompts(schema_profile, settings),
    )
    response, _call_error, response_duration_ms = await _call_model(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        system_prompt=(
            "You repair AuthorOutputContract JSON so it exactly matches the schema. "
            "Return JSON only."
        ),
        user_prompt=repair_prompt,
        purpose="contract_schema_repair",
        settings=settings,
    )
    if response is None:
        return None
    _write_response_artifacts(
        responses_dir=responses_dir,
        stage=f"{source_stage}.contract_schema_repair",
        prompt=repair_prompt,
        response_text=response.text,
    )
    _record_model_call(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        response=response,
        purpose="contract_schema_repair",
        settings=settings,
        duration_ms=response_duration_ms,
    )
    repaired_payload = await _extract_json_or_repair(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        responses_dir=responses_dir,
        source_stage=f"{source_stage}.contract_schema_repair",
        raw_text=response.text,
        expected_shape='{"author_output_contract":{}}',
        settings=settings,
    )
    candidate = (
        _contract_payload_from_model(repaired_payload)
        if isinstance(repaired_payload, dict)
        else None
    )
    if not isinstance(candidate, dict):
        return None
    candidate = _finalize_contract_payload(
        candidate,
        schema_profile=schema_profile,
        user_description=user_description,
        contract_reference=contract_reference,
    )
    try:
        return _validate_contract_payload_strict(
            candidate,
            schema_profile=schema_profile,
            contract_reference=contract_reference,
            user_description=user_description,
        )
    except (ValidationError, ValueError):
        return None


async def _validate_contract_or_repair(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient,
    responses_dir: Path,
    source_stage: str,
    payload: dict[str, Any],
    schema_profile: dict[str, Any],
    user_description: str,
    contract_reference: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> tuple[AuthorOutputContract | None, str | None]:
    payload = _finalize_contract_payload(
        payload,
        schema_profile=schema_profile,
        user_description=user_description,
        contract_reference=contract_reference,
    )
    try:
        return (
            _validate_contract_payload_strict(
                payload,
                schema_profile=schema_profile,
                contract_reference=contract_reference,
                user_description=user_description,
            ),
            None,
        )
    except (ValidationError, ValueError) as exc:
        validation_error = str(exc)
        validation_issue_details = _contract_validation_issue_details(exc)
    repaired = await _repair_contract_schema_payload(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        responses_dir=responses_dir,
        source_stage=source_stage,
        payload=payload,
        validation_error=validation_error,
        validation_issue_details=validation_issue_details,
        schema_profile=schema_profile,
        user_description=user_description,
        contract_reference=contract_reference,
        settings=settings,
    )
    if repaired is None:
        return None, validation_error
    return repaired, None


def _normalise_contract_payload(
    *,
    payload: dict[str, Any],
    workflow_type: str,
    schema_profile: dict[str, Any],
    output_contract_hint: dict[str, Any] | None,
) -> AuthorOutputContract:
    defaults = _contract_defaults(
        workflow_type=workflow_type,
        schema_profile=schema_profile,
        output_contract_hint=output_contract_hint,
    )
    data = dict(defaults)
    data.update(payload)
    input_file = str(data.get("input_file") or "")
    if input_file and not input_file.startswith(("uploads/", "generated/data/")):
        data["input_file"] = defaults["input_file"]
    row_output = str(data.get("row_level_output_file") or "")
    if row_output and "/" not in row_output:
        data["row_level_output_file"] = f"outputs/{row_output}"
    data["summary_output_files"] = [
        f"outputs/{path}" if isinstance(path, str) and "/" not in path else path
        for path in list(data.get("summary_output_files") or [])
    ]
    data["exception_output_files"] = [
        f"outputs/{path}" if isinstance(path, str) and "/" not in path else path
        for path in list(data.get("exception_output_files") or [])
    ]
    if not data.get("output_columns"):
        data["output_columns"] = list(data.get("input_columns") or [])
    required = list(data.get("required_output_columns") or [])
    output_columns = list(data.get("output_columns") or [])
    for column in required:
        if column not in output_columns:
            output_columns.append(column)
    data["output_columns"] = output_columns
    checks = []
    for item in data.get("validation_checks") or []:
        if isinstance(item, str):
            checks.append(
                {
                    "check_id": item,
                    "layer": item,
                    "name": item.replace("_", " ").title(),
                    "required": True,
                }
            )
        else:
            checks.append(item)
    data["validation_checks"] = checks
    classifications = []
    for idx, item in enumerate(data.get("classification_rules") or []):
        if isinstance(item, str):
            classifications.append({"name": f"rule_{idx + 1}", "description": item})
        else:
            classifications.append(item)
    data["classification_rules"] = classifications
    calculated = []
    for idx, item in enumerate(data.get("calculated_fields") or []):
        if isinstance(item, str):
            calculated.append(
                {
                    "name": f"calculated_field_{idx + 1}",
                    "description": item,
                    "formula": item,
                }
            )
        else:
            calculated.append(item)
    data["calculated_fields"] = calculated
    tolerances = {}
    for key, value in dict(data.get("tolerances") or {}).items():
        try:
            tolerances[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    data["tolerances"] = tolerances
    return AuthorOutputContract.model_validate(data)


def _normalise_files_payload(payload: dict[str, Any]) -> dict[str, str] | None:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return None
    files: dict[str, str] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            return None
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None
        files[path.replace("\\", "/")] = content
    return files if files else None


def _missing_required_files(paths: list[str]) -> list[str]:
    missing: list[str] = []
    if _REQUIRED_AGENT_PATH not in paths:
        missing.append(_REQUIRED_AGENT_PATH)
    if not any(path.endswith(_REQUIRED_TEST_SUFFIX) for path in paths):
        missing.append("generated/tests/test_agent.py")
    return missing


def _artifact_context_for_repair(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> dict[str, Any]:
    required_paths = contract.all_required_output_paths()
    produced_artifact_paths: list[str] = []
    for folder in ("outputs", "reports"):
        base = workspace / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                produced_artifact_paths.append(path.relative_to(workspace).as_posix())
    context: dict[str, Any] = {
        "required_artifacts": _required_artifact_specs(contract),
        "produced_artifact_paths": produced_artifact_paths,
        "produced_required_artifact_paths": [
            path for path in required_paths if (workspace / path).is_file()
        ],
        "missing_required_artifact_paths": [
            path for path in required_paths if not (workspace / path).is_file()
        ],
    }
    row_output = workspace / contract.row_level_output_file
    if row_output.is_file():
        try:
            with row_output.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                context["row_output_header"] = list(reader.fieldnames or [])
                sample_rows: list[dict[str, str]] = []
                for row in reader:
                    sample_rows.append(dict(row))
                    if len(sample_rows) >= 3:
                        break
                context["row_output_sample_rows"] = sample_rows
        except Exception:
            pass
    report_path = workspace / _primary_report_path(contract)
    if report_path.is_file():
        report_text = report_path.read_text(encoding="utf-8", errors="ignore").strip()
        if report_text:
            context["report_excerpt"] = report_text[:2000]
    return context


def _missing_required_files_error(missing: list[str]) -> ErrorCode:
    if any(path.endswith(_REQUIRED_TEST_SUFFIX) for path in missing):
        return ErrorCode.AUTHOR_TEST_GENERATION_FAILED
    if _REQUIRED_AGENT_PATH in missing:
        return ErrorCode.AUTHOR_CODE_GENERATION_FAILED
    return ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE


def _record_json_artifact_provenance(
    *,
    workspace: Path,
    rel: str,
    file_provenance: dict[str, dict[str, Any]],
) -> None:
    path = workspace / rel
    file_hash = _sha256_file(path)
    file_provenance[rel] = {
        "path": rel,
        "reference_hash": None,
        "model_output_hash": file_hash,
        "final_hash": file_hash,
        "contribution": "model_generated",
    }


def _safety_issues_for_files(files: dict[str, str]) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}
    for rel, content in files.items():
        file_issues = _scan_model_code(content)
        if file_issues:
            issues[rel] = sorted(set(file_issues))
    return issues


def _safety_repair_prompt(
    *,
    reviewed_contract: AuthorOutputContract,
    files: dict[str, str],
    unsafe_findings: dict[str, list[str]],
) -> str:
    unsafe_only = [
        {"path": path, "content": files[path]}
        for path in sorted(unsafe_findings)
        if path in files
    ]
    unsafe_paths = [item["path"] for item in unsafe_only]
    invalidity_rules = [
        f"If {', '.join(labels)} remains anywhere in {path}, the patch is invalid."
        for path, labels in sorted(unsafe_findings.items())
    ]
    return json.dumps(
        {
            "stage": "safety_repair",
            "unsafe_findings": unsafe_findings,
            "unsafe_violation_context": _unsafe_violation_context(
                files=files,
                issues=unsafe_findings,
            ),
            "forbidden_constructs": _forbidden_construct_labels(),
            "author_output_contract": _compact_codegen_contract(reviewed_contract),
            "files": unsafe_only,
            "required_response_shape": {
                "files": [
                    {"path": path, "content": "complete safe replacement Python source"}
                    for path in unsafe_paths
                ]
            },
            "instruction": (
                "Repair only the unsafe files listed in unsafe_findings. Return strict "
                "JSON only with `files` containing complete replacement Python source "
                "for every listed unsafe path. Remove each forbidden construct "
                "completely. Do not return unchanged safe files. Do not rewrite tests "
                "unless a test file itself is listed in unsafe_findings. Never use "
                "eval(), exec(), compile(), dynamic import, subprocess, os.system, "
                "shell calls, network calls, or filesystem access outside the "
                "workspace. Never parse formulas, conditions, filters, or business "
                "rules with eval or another dynamic execution primitive. Do not "
                "replace eval() with exec(), compile(), getattr-based dynamic "
                "dispatch, or another unsafe equivalent. Use explicit "
                "conditionals, safe parsers, or contract-backed deterministic helpers "
                "instead. Never write backend orchestration or audit control files "
                f"({', '.join(sorted(_BACKEND_MANAGED_ARTIFACT_PATHS))}, "
                "generated/model_responses/, generated/debug/). Generated agents may "
                "only write CLI-declared outputs and contract workflow artifacts under "
                "outputs/ and reports/. The same safety scanner will run again after repair. "
                + " ".join(invalidity_rules)
            ),
        },
        indent=2,
    )


def _safety_failure_detail(
    *,
    issues: dict[str, list[str]],
    files: dict[str, str],
    intro: str,
) -> str:
    lines = [intro]
    for context in _unsafe_violation_context(files=files, issues=issues):
        lines.append(
            f"path={context['path']} forbidden_construct={context['construct']}"
        )
        if context.get("line_number") is not None:
            lines.append(f"line_number={context['line_number']}")
        snippet = str(context.get("snippet") or "").strip()
        if snippet:
            lines.append("snippet:\n" + snippet)
    return "\n".join(lines)


def _write_model_files(
    *,
    workspace: Path,
    files: dict[str, str],
    reference_hashes: dict[str, str],
    file_provenance: dict[str, dict[str, Any]],
) -> tuple[list[str], str | None]:
    written: list[str] = []
    required_material_failure: str | None = None
    for rel_path, content in files.items():
        rel = str(rel_path).replace("\\", "/")
        if not _validate_generated_path(rel):
            raise ValueError(f"Model attempted to write disallowed path: {rel}")
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(rel)

        model_hash = _sha256_text(content)
        final_hash = _sha256_file(dest)
        ref_hash = reference_hashes.get(rel)
        if ref_hash is None:
            contribution = "model_generated"
        elif ref_hash == model_hash:
            contribution = "reference_reemitted_without_material_change"
            if rel == _REQUIRED_AGENT_PATH or rel.endswith(_REQUIRED_TEST_SUFFIX):
                required_material_failure = rel
        else:
            contribution = "model_modified"
        patch_rel = f"generated/model_responses/{rel.replace('/', '__')}.patch.json"
        patch_payload = {
            "path": rel,
            "reference_hash": ref_hash,
            "model_output_hash": model_hash,
            "final_hash": final_hash,
            "contribution": contribution,
        }
        _write_json(workspace / patch_rel, patch_payload)
        file_provenance[rel] = patch_payload
    return written, required_material_failure


def _record_authoring_provenance(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    contributed_files: list[str],
    stages_completed: list[str],
    file_provenance: dict[str, dict[str, Any]],
    summary_rel: str,
) -> dict[str, Any]:
    event_provenance = author_provenance_from_events(event_log.read_all(session_id))
    provenance = {
        "model_contributed_files": sorted(set(contributed_files)),
        "model_stages": list(stages_completed),
        "file_provenance": list(file_provenance.values()),
        "summary_path": summary_rel,
        "model_by_stage": event_provenance.get("model_by_stage", {}),
    }
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "model_authoring_provenance",
            "stage": stage_label,
            **provenance,
        },
        step=step,
    )
    return provenance


async def run_model_authoring_pipeline(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient | None,
    workspace: Path,
    user_description: str,
    schema_profile: dict[str, Any],
    reference_scaffold_root: Path | None,
    workflow_type: str,
    template_hint: str | None = None,
    output_contract_hint: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> ModelAuthoringResult:
    """Run bounded LLM-first Author stages and persist model-authored artifacts."""
    if model_client is None:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING,
            message=AUTHOR_MODEL_REQUIRED_MESSAGE,
        )

    reference_files = load_reference_scaffold(reference_scaffold_root)
    reference_hashes = {path: _sha256_text(content) for path, content in reference_files.items()}
    stages_completed: list[str] = []
    contributed_files: list[str] = []
    file_provenance: dict[str, dict[str, Any]] = {}
    responses_dir = workspace / "generated" / "model_responses"
    responses_dir.mkdir(parents=True, exist_ok=True)

    prompt_schema = _compact_schema_profile(schema_profile)
    file_profile = _contract_file_profile_metadata(prompt_schema)
    bundled_bank_reference = _is_bundled_bank_reference_sample(
        template_hint=template_hint,
        schema_profile=prompt_schema,
    )
    bundled_bank_reference_mode = (
        _bank_reference_scaffold_enabled(settings) and bundled_bank_reference
    )
    custom_bank_categoriser = _is_custom_bank_categoriser_sample(
        template_hint=template_hint,
        schema_profile=prompt_schema,
    )
    bundled_bank_scaffold = (
        _bundled_bank_reference_contract_scaffold(schema_profile=prompt_schema)
        if bundled_bank_reference_mode
        else None
    )
    if custom_bank_categoriser:
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "custom_bank_categoriser_guidance",
                "stage": "contract_planning",
                "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
                "message": _CUSTOM_BANK_CATEGORISER_WARNING,
                "reference_scaffold_files": list(reference_files.keys()),
            },
            step=step,
        )

    contract_shape = (
        "JSON object with `author_output_contract` containing the full "
        "AuthorOutputContract fields: workflow_type, input_file, input_format, "
        "selected_sheet, row_level_output_file, summary_output_files, "
        "exception_output_files, primary_row_key, required_output_columns, "
        "optional_output_columns, output_column_semantics, calculated_fields, "
        "formula_input_columns, formula_output_columns, tolerances, summary_group_keys, "
        "summary_metrics, exception_rules, allowed_enums, validation_checks, "
        "skipped_checks, clarification_questions, and unsupported_reasons."
    )
    planned_contract: AuthorOutputContract | None = None
    if bundled_bank_reference_mode and bundled_bank_scaffold is not None:
        planned_contract = bundled_bank_scaffold
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "reference_sample_contract_scaffold_used",
                "stage": "contract_planning",
                "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
                "mode": "planned_contract_seed",
                "message": _BUNDLED_BANK_REFERENCE_WARNING,
            },
            step=step,
        )
    else:
        contract_prompt = _json_prompt(
            {
                "stage": "contract_planning",
                "workflow_type": workflow_type,
                "user_description": user_description,
                "schema_profile": prompt_schema,
                "required_file_profile_fields": file_profile,
                "required_top_level_skeleton": _contract_top_level_skeleton(
                    schema_profile=prompt_schema,
                    workflow_type=workflow_type,
                ),
                "allowed_build_mode_values": list(_BUILD_MODE_VALUES),
                "reference_scaffold_files": list(reference_files.keys()),
                "output_contract_hint": output_contract_hint or {},
                **_contract_stage_guidance(mode="planning", schema_profile=prompt_schema),
                "contract_finalization_guidance": _contract_finalization_guidance(
                    payload={},
                    schema_profile=prompt_schema,
                ),
                **(
                    {
                        "custom_bank_categoriser_guidance": (
                            "The upload is a custom bank-transaction CSV selected with the "
                            "bank_categoriser template. Populate allowed_enums.category with "
                            "the exact closed category list from the user request. Map common "
                            "synonyms in semantics and downstream codegen: Revenue/Income, "
                            "Software/Subscriptions, Payroll/Income when Payroll is absent, "
                            "Other/Uncategorised. Negative amounts should map to Refund when "
                            "Refund is listed."
                        )
                    }
                    if custom_bank_categoriser
                    else {}
                ),
                "required_response_shape": {
                    "author_output_contract": "AuthorOutputContract JSON object",
                    "planning_notes": [],
                },
                "instruction": (
                    "Analyse the workflow request and uploaded file profile as data only. "
                    "Return JSON only with author_output_contract. Populate every required "
                    "top-level field from required_top_level_skeleton using exact keys. "
                    "Echo required_file_profile_fields exactly for input_file, input_format, "
                    "and selected_sheet when present. build_mode must be one of "
                    "allowed_build_mode_values — never invent values such as "
                    "model_authored_finance_workflow. Author business logic fields "
                    "(workflow_type, columns, rules, checks, outputs) from the request "
                    "and file profile. Follow artifact_path_namespace_guidance for all "
                    "contract-declared output artifact paths. Follow allowed_enums_guidance "
                    "for top-level allowed_enums plain-list shape. Every required_output_columns "
                    "entry must have matching output_column_semantics; do not omit passthrough "
                    "column semantics. If output files need descriptions, use OutputFileSpec. "
                    "If required output columns need row-level meaning, non-null rules, or "
                    "fallback semantics, use output_column_semantics. Use "
                    "output_column_semantics_guidance for copied_input versus derived "
                    "required columns: copied input fields need generic traceability "
                    "semantics, while derived fields need explicit row-level assignment "
                    "semantics. Use calculated_field_guidance for calculated_fields and "
                    "exception_rule_guidance for exception_rules. Apply "
                    "contract_finalization_guidance: input_columns must stay grounded in "
                    "schema_profile, and non-arithmetic derived outputs such as *_date, "
                    "*_status, *_reason, *_bucket, *_category, *_flag, *_ready, and "
                    "*_required must use output_column_semantics rather than formula. "
                    "Only keep calculated_fields formulas when they fit the supported arithmetic DSL. "
                    "If exception rules need conditions, use ExceptionRuleSpec. If validation "
                    "checks include formulas, use ValidationCheckSpec with check_type formula. "
                    "Follow validation_check_guidance: every validation check needs "
                    "check_id and layer, and you must never nest allowed_enums, "
                    "allowed_values, categories, min_value, or max_value inside "
                    "validation_checks; use top-level allowed_enums for category "
                    "membership and formula-based checks for numeric ranges. "
                    "If the user lists allowed labels, categories, statuses, or classes, "
                    "put them in allowed_enums and do not create a hard validation check "
                    "that forbids one of those allowed or fallback values. "
                    "When the user prompt contains an explicit closed category list "
                    '(for example "standard categories A, B, C" or "classify into"), '
                    "populate allowed_enums.category with that exact closed list. "
                    "If the prompt and uploaded file describe materially different finance "
                    "workflows, set build_mode to clarification and populate "
                    "clarification_questions instead of inventing a forced contract."
                ),
            },
        )
        _record_stage_prompt_metrics(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            purpose="contract_planning",
            prompt=contract_prompt,
            compact_mode=False,
        )
        contract_response, contract_call_error, contract_duration_ms = await _call_model(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            system_prompt=(
                "You are a finance agent authoring assistant. Reference scaffolds are patterns "
                "only — adapt the output contract to the uploaded file and user request. "
                "Respond with JSON only using exact AuthorOutputContract keys."
            ),
            user_prompt=contract_prompt,
            purpose="contract_planning",
            settings=settings,
        )
        if contract_response is None:
            return ModelAuthoringResult(
                ok=False,
                error_code=ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
                message="Contract planning model call failed.",
                technical_detail=contract_call_error,
            )

        contract_raw = contract_response.text
        _write_response_artifacts(
            responses_dir=responses_dir,
            stage="contract_planning",
            prompt=contract_prompt,
            response_text=contract_raw,
        )
        _record_model_call(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            response=contract_response,
            purpose="contract_planning",
            settings=settings,
            duration_ms=contract_duration_ms,
        )
        contract_payload = await _extract_json_or_repair(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            responses_dir=responses_dir,
            source_stage="contract_planning",
            raw_text=contract_raw,
            expected_shape=contract_shape,
            settings=settings,
        )
        contract_plan_payload = (
            _contract_payload_from_model(contract_payload)
            if isinstance(contract_payload, dict)
            else None
        )
        if not isinstance(contract_plan_payload, dict):
            return _contract_planning_failed_result(
                technical_detail="Model did not return a valid AuthorOutputContract plan.",
                stages=["contract_planning"],
            )

        clarifications = _extract_planning_clarifications(
            planning_payload=contract_payload if isinstance(contract_payload, dict) else None,
            contract_payload=contract_plan_payload,
        )
        if clarifications:
            return _contract_clarification_result(
                clarifications[0],
                stages=["contract_planning"],
            )

        planned_contract, planning_error = await _validate_contract_or_repair(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            responses_dir=responses_dir,
            source_stage="contract_planning",
            payload=contract_plan_payload,
            schema_profile=prompt_schema,
            user_description=user_description,
            settings=settings,
        )
        if planned_contract is None:
            clarifications = _extract_planning_clarifications(
                planning_payload=contract_payload if isinstance(contract_payload, dict) else None,
                contract_payload=contract_plan_payload,
            )
            if clarifications:
                return _contract_clarification_result(
                    clarifications[0],
                    stages=["contract_planning"],
                )
            return _contract_planning_failed_result(
                technical_detail=planning_error,
                stages=["contract_planning"],
            )

    if planned_contract.clarification_questions:
        return _contract_clarification_result(
            planned_contract.clarification_questions[0],
            stages=["contract_planning"],
        )
    if planned_contract.build_mode == "clarification":
        return _contract_clarification_result(
            ClarificationQuestion(
                question=(
                    "The uploaded file and workflow description appear to describe "
                    "different finance workflows. Please clarify which workflow this run "
                    "should follow."
                ),
                reason="Contract planning returned build_mode clarification without a question.",
            ),
            stages=["contract_planning"],
        )

    if bundled_bank_reference_mode and bundled_bank_scaffold is not None:
        planned_contract = apply_bundled_bank_reference_golden_policy(planned_contract)
    elif _is_expense_exception_review_context(
        template_hint=template_hint,
        schema_profile=prompt_schema,
    ):
        stage_expense_exception_golden(workspace=workspace)
        planned_contract = apply_expense_exception_golden_policy(planned_contract)

    _write_json(
        workspace / "generated" / "model_contract_plan.json",
        planned_contract.model_dump(mode="json"),
    )
    _record_json_artifact_provenance(
        workspace=workspace,
        rel="generated/model_contract_plan.json",
        file_provenance=file_provenance,
    )
    contributed_files.append("generated/model_contract_plan.json")
    stages_completed.append("contract_planning")

    compact_prompts = _use_compact_contract_prompts(prompt_schema, settings)
    review_payload: dict[str, Any] = {
        "stage": "contract_review",
        "workflow_type": workflow_type,
        "user_description": user_description,
        "schema_profile": prompt_schema,
        "required_file_profile_fields": file_profile,
        "required_top_level_skeleton": _contract_top_level_skeleton(
            schema_profile=prompt_schema,
            workflow_type=workflow_type,
        ),
        "allowed_build_mode_values": list(_BUILD_MODE_VALUES),
        **_contract_stage_guidance(mode="review", schema_profile=prompt_schema),
        "contract_finalization_guidance": _contract_finalization_guidance(
            payload=planned_contract.model_dump(mode="json"),
            schema_profile=prompt_schema,
            contract_reference=planned_contract.model_dump(mode="json"),
        ),
        "uploaded_input_columns": list(prompt_schema.get("columns") or []),
        "planned_contract_input_columns": list(planned_contract.input_columns),
        "planned_author_output_contract": _contract_for_prompt(
            planned_contract,
            compact=compact_prompts,
        ),
        "instruction": (
            "Review the planned AuthorOutputContract for consistency with the "
            "uploaded schema and user request. Return JSON only with "
            "`reviewed_contract` as the final full AuthorOutputContract. Follow "
            "contract_return_shape_guidance: return only AuthorOutputContract fields, "
            "include input_format matching the uploaded input type, and never emit "
            "top-level prompt guidance or schema-path metadata. Follow "
            "artifact_path_namespace_guidance for all contract-declared output "
            "artifact paths. Follow allowed_enums_guidance for top-level "
            "allowed_enums plain-list shape. Fix "
            "missing required output columns, deliverables, validation checks, "
            "exception rules, formula columns, output_column_semantics, "
            "tolerances, summary keys/metrics, requested_deliverables, or "
            "unsupported/clarification fields. "
            "Only keep an extra summary or exception file required when the user "
            "explicitly asked for that separate artifact with DeliverableSpec "
            "required=true and source='user_explicit'. Plain path strings in "
            "requested_deliverables are informational only. Downgrade "
            "summary_by_category, review_rows, exceptions, or similar report-derived "
            "CSVs to optional unless user-explicit. A report requirement "
            "for uncertain or human-review rows does not by itself justify a "
            "required exceptions CSV. Category counts and review rows belong "
            "inside the validation report unless the user explicitly requested "
            "separate files. "
            "Every required_output_columns entry must have explicit row-level "
            "semantics and fallback semantics in output_column_semantics. Keep "
            "copied_input semantics only for passthrough input columns. If a "
            "required column is not present in input_columns, do not mark it as "
            "copied_input — define explicit derived semantics instead. Only place "
            "passthrough input columns in required_output_columns when the "
            "workflow truly requires them as non-null output fields. Use "
            "output_column_semantics_guidance when clarifying copied-input "
            "versus derived semantics. Preserve the planned input column universe "
            "from planned_contract_input_columns and do not replace it with an "
            "unrelated workflow domain. Use uploaded_input_columns as the source "
            "of truth for raw input column grounding. Only "
            "keep a calculated_fields formula when it fits the supported "
            f"{_FORMULA_DSL_DESCRIPTION}; otherwise remove the unsupported formula "
            "and express that field through output_column_semantics instead. "
            "Non-arithmetic derived outputs such as *_date, *_status, *_reason, "
            "*_bucket, *_category, *_flag, *_ready, and *_required must use "
            "output_column_semantics rather than formula. "
            "Use calculated_field_guidance for calculated_fields and "
            "exception_rule_guidance for exception_rules. "
            "SummaryMetricSpec must use only the allowed fields and must never "
            "include `condition`; convert conditional metrics to `filter` or "
            "remove/simplify them. "
            "Reject or repair any required validation_check that contradicts "
            "allowed_enums, an explicit user-requested value, or "
            "output_column_semantics fallback_value_semantics. If a value is an "
            "allowed fallback, keep it valid and represent review/counting needs "
            "through summary_metrics or report semantics rather than a hard "
            "failure. When the user prompt contains an explicit closed category "
            'list (for example "standard categories A, B, C" or "classify into"), '
            "ensure allowed_enums.category contains that exact closed list. "
            "summary_metrics may be plain strings or SummaryMetricSpec objects "
            "(including formula, input_columns, output_column). "
            "requested_deliverables and missing_deliverables may be plain path "
            "strings or DeliverableSpec objects — do not flatten structured "
            "deliverables into invalid shapes. Every validation check needs "
            "check_id and layer. Structured DeliverableSpec objects "
            "must use output_path, not path, and must include name. Echo "
            "required_file_profile_fields.input_file exactly. Do not emit "
            "output_format because it is not an AuthorOutputContract field. Do "
            "not introduce deterministic template assumptions."
        ),
    }
    review_prompt = _json_prompt(review_payload)
    _record_stage_prompt_metrics(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        purpose="contract_review",
        prompt=review_prompt,
        compact_mode=compact_prompts,
    )
    review_client_override = None
    if compact_prompts and _llm_provider_name(settings) == "deepseek":
        review_client_override = _client_for_purpose(
            model_client=model_client,
            purpose="code_generation",
            settings=settings,
        )
    review_response, review_call_error, review_duration_ms = await _call_model(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        system_prompt=(
            "You are a strict operator for model-authored finance agent output "
            "contracts. Return corrected JSON only."
        ),
        user_prompt=review_prompt,
        purpose="contract_review",
        settings=settings,
        client_override=review_client_override,
    )
    if review_response is None:
        if bundled_bank_reference_mode and bundled_bank_scaffold is not None:
            event_log.append(
                session_id=session_id,
                kind=EventKind.DECISION_INPUT,
                actor_type=ActorType.SYSTEM,
                payload={
                    "kind": "reference_sample_contract_scaffold_used",
                    "stage": "contract_review",
                    "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
                    "mode": "review_model_call_failed_fallback",
                    "message": _BUNDLED_BANK_REFERENCE_WARNING,
                    "technical_detail": review_call_error,
                },
                step=step,
            )
            reviewed_contract = bundled_bank_scaffold
            review_payload = {
                "reviewed_contract": reviewed_contract.model_dump(mode="json"),
                "review_fallback_reason": review_call_error,
            }
            review_raw = json.dumps(review_payload, indent=2)
            goto_review_write = True
        else:
            return _contract_review_failed_result(
                technical_detail=review_call_error,
                stages=stages_completed,
            )
    else:
        goto_review_write = False
        review_raw = review_response.text
    if not goto_review_write:
        _write_response_artifacts(
            responses_dir=responses_dir,
            stage="contract_review",
            prompt=review_prompt,
            response_text=review_raw,
        )
        _record_model_call(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            response=review_response,
            purpose="contract_review",
            contributed_files=["generated/model_contract_plan.json"],
            settings=settings,
            duration_ms=review_duration_ms,
        )
        review_payload = await _extract_json_or_repair(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            responses_dir=responses_dir,
            source_stage="contract_review",
            raw_text=review_raw,
            expected_shape=contract_shape,
            settings=settings,
        )
        reviewed_payload = (
            _contract_payload_from_model(review_payload) if isinstance(review_payload, dict) else None
        )
        if not isinstance(reviewed_payload, dict):
            if bundled_bank_reference_mode and bundled_bank_scaffold is not None:
                event_log.append(
                    session_id=session_id,
                    kind=EventKind.DECISION_INPUT,
                    actor_type=ActorType.SYSTEM,
                    payload={
                        "kind": "reference_sample_contract_scaffold_used",
                        "stage": "contract_review",
                        "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
                        "mode": "review_payload_invalid_fallback",
                        "message": _BUNDLED_BANK_REFERENCE_WARNING,
                    },
                    step=step,
                )
                reviewed_contract = bundled_bank_scaffold
            else:
                return _contract_review_failed_result(
                    technical_detail="Model contract review did not return a final AuthorOutputContract.",
                    stages=stages_completed + ["contract_review"],
                )
        else:
            reviewed_contract, review_error = await _validate_contract_or_repair(
                session_id=session_id,
                event_log=event_log,
                step=step,
                stage_label=stage_label,
                model_client=model_client,
                responses_dir=responses_dir,
                source_stage="contract_review",
                payload=reviewed_payload,
                schema_profile=prompt_schema,
                user_description=user_description,
                contract_reference=planned_contract.model_dump(mode="json"),
                settings=settings,
            )
            if reviewed_contract is None:
                if bundled_bank_reference_mode and bundled_bank_scaffold is not None:
                    event_log.append(
                        session_id=session_id,
                        kind=EventKind.DECISION_INPUT,
                        actor_type=ActorType.SYSTEM,
                        payload={
                            "kind": "reference_sample_contract_scaffold_used",
                            "stage": "contract_review",
                            "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
                            "mode": "review_validation_failed_fallback",
                            "message": _BUNDLED_BANK_REFERENCE_WARNING,
                            "technical_detail": review_error,
                        },
                        step=step,
                    )
                    reviewed_contract = bundled_bank_scaffold
                else:
                    return _contract_review_failed_result(
                        technical_detail=review_error,
                        stages=stages_completed + ["contract_review"],
                    )
    assert reviewed_contract is not None
    if bundled_bank_reference_mode and bundled_bank_scaffold is not None:
        reviewed_contract = apply_bundled_bank_reference_golden_policy(reviewed_contract)
    elif _is_expense_exception_review_context(
        template_hint=template_hint,
        schema_profile=prompt_schema,
    ):
        stage_expense_exception_golden(workspace=workspace)
        reviewed_contract = apply_expense_exception_golden_policy(reviewed_contract)
    _write_json(
        workspace / "generated" / "model_contract_review.json",
        {
            "review_payload": review_payload or {},
            "reviewed_contract": reviewed_contract.model_dump(mode="json"),
        },
    )
    _write_json(
        workspace / "generated" / "author_output_contract.json",
        reviewed_contract.model_dump(mode="json"),
    )
    _record_json_artifact_provenance(
        workspace=workspace,
        rel="generated/model_contract_review.json",
        file_provenance=file_provenance,
    )
    _record_json_artifact_provenance(
        workspace=workspace,
        rel="generated/author_output_contract.json",
        file_provenance=file_provenance,
    )
    contributed_files.extend(
        [
            "generated/model_contract_review.json",
            "generated/author_output_contract.json",
        ]
    )
    stages_completed.append("contract_review")

    codegen_prompt = _codegen_prompt(
        workflow_type=workflow_type,
        user_description=user_description,
        reviewed_contract=reviewed_contract,
        template_hint=template_hint,
        schema_profile=prompt_schema,
        enable_bundled_bank_reference_guidance=bundled_bank_reference_mode,
    )
    codegen_system_prompt = (
        "You write generated/agent.py for finance CSV agents. "
        "Return raw Python source only — no markdown, JSON, or explanation."
    )
    codegen_timeout = (
        _timeout_seconds_for_purpose("code_generation", settings) if settings else 900.0
    )
    codegen_max_tokens = _codegen_max_tokens_for_purpose("code_generation", settings)
    codegen_client_override = _bundled_bank_reference_demo_client_override(
        model_client=model_client,
        settings=settings,
        purpose="code_generation",
        enabled=bundled_bank_reference_mode,
    )
    debug_meta = _write_codegen_debug_artifacts(
        workspace=workspace,
        system_prompt=codegen_system_prompt,
        user_prompt=codegen_prompt,
        reviewed_contract=reviewed_contract,
        model_client=model_client,
        settings=settings,
        max_tokens=codegen_max_tokens,
        timeout_seconds=codegen_timeout,
        client_override=codegen_client_override,
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "codegen_prompt_metrics",
            "stage": stage_label,
            "purpose": "code_generation",
            "response_format": "raw_python",
            **debug_meta,
        },
        step=step,
    )
    codegen_response, codegen_call_error, codegen_duration_ms = await _call_model(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        system_prompt=codegen_system_prompt,
        user_prompt=codegen_prompt,
        purpose="code_generation",
        settings=settings,
        client_override=codegen_client_override,
    )
    if codegen_response is None:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_CODE_GENERATION_FAILED,
            message="Code generation model call failed.",
            technical_detail=codegen_call_error,
            stages=stages_completed,
        )

    codegen_raw = codegen_response.text
    _write_response_artifacts(
        responses_dir=responses_dir,
        stage="code_generation",
        prompt=codegen_prompt,
        response_text=codegen_raw,
    )
    _record_model_call(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        response=codegen_response,
        purpose="code_generation",
        settings=settings,
        duration_ms=codegen_duration_ms,
    )
    agent_source, parse_error = _parse_codegen_agent_source(codegen_raw)
    if agent_source is None:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_CODE_GENERATION_FAILED,
            message="Code generation did not return valid agent.py source.",
            technical_detail=_codegen_failure_detail(
                failure="malformed_agent_source",
                error=parse_error,
            ),
            stages=stages_completed + ["code_generation"],
        )

    code_plan = {
        "files_to_create": [_REQUIRED_AGENT_PATH],
        "reasoning_summary": "Model-authored runnable agent file (raw Python response).",
        "dependencies": [],
        "input_paths": [reviewed_contract.input_file],
        "output_paths": reviewed_contract.all_required_output_paths(),
        "tests_to_generate": ["generated/tests/test_agent.py"],
    }
    files_payload = {_REQUIRED_AGENT_PATH: agent_source}

    _write_json(workspace / "generated" / "model_code_plan.json", code_plan)
    contributed_files.append("generated/model_code_plan.json")

    code_stage = "code_generation"
    if any(path in reference_hashes for path in files_payload):
        code_stage = "code_adaptation"

    test_prompt = _test_generation_prompt(
        workflow_type=workflow_type,
        user_description=user_description,
        reviewed_contract=reviewed_contract,
        template_hint=template_hint,
        schema_profile=prompt_schema,
        enable_bundled_bank_reference_guidance=bundled_bank_reference_mode,
    )
    test_client_override = _bundled_bank_reference_demo_client_override(
        model_client=model_client,
        settings=settings,
        purpose="test_generation",
        enabled=bundled_bank_reference_mode,
    )
    test_response, test_call_error, test_duration_ms = await _call_model(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        system_prompt=(
            "You author pytest checks for finance agents. Return JSON only in the "
            "requested files-list shape."
        ),
        user_prompt=test_prompt,
        purpose="test_generation",
        settings=settings,
        client_override=test_client_override,
    )
    if test_response is None:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_TEST_GENERATION_FAILED,
            message="Test generation model call failed.",
            technical_detail=test_call_error,
            stages=stages_completed + [code_stage],
        )
    test_raw = test_response.text
    _write_response_artifacts(
        responses_dir=responses_dir,
        stage="test_generation",
        prompt=test_prompt,
        response_text=test_raw,
    )
    _record_model_call(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        response=test_response,
        purpose="test_generation",
        settings=settings,
        duration_ms=test_duration_ms,
    )
    test_payload = await _extract_json_or_repair(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        responses_dir=responses_dir,
        source_stage="test_generation",
        raw_text=test_raw,
        expected_shape='{"files":[{"path":"generated/tests/test_agent.py","content":"..."}],"notes":"...","assumptions":[]}',
        settings=settings,
    )
    test_files = _normalise_files_payload(test_payload) if isinstance(test_payload, dict) else None
    if not test_files:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_TEST_GENERATION_FAILED,
            message="Test generation did not return a strict files list.",
            stages=stages_completed + [code_stage, "test_generation"],
        )
    files_payload.update(test_files)

    missing = _missing_required_files(sorted(files_payload))
    if missing:
        missing_prompt = _json_prompt(
            {
                "stage": "missing_files_repair",
                "missing_files": missing,
                "workflow_type": workflow_type,
                "author_output_contract": _compact_codegen_contract(reviewed_contract),
                "required_cli_interface": _generated_agent_cli_contract(reviewed_contract),
                "instruction": (
                    "Return strict JSON only with `files` containing exactly the "
                    "missing complete Python files. Do not include explanations."
                ),
            }
        )
        missing_response, missing_call_error, missing_duration_ms = await _call_model(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            system_prompt="You return missing generated Python files as JSON only.",
            user_prompt=missing_prompt,
            purpose="missing_file_repair",
            settings=settings,
        )
        if missing_response is None:
            return ModelAuthoringResult(
                ok=False,
                error_code=_missing_required_files_error(missing),
                message=f"Model did not provide required generated files: {missing}",
                technical_detail=missing_call_error,
                stages=stages_completed + [code_stage, "test_generation"],
            )
        _write_response_artifacts(
            responses_dir=responses_dir,
            stage="missing_files_repair",
            prompt=missing_prompt,
            response_text=missing_response.text,
        )
        _record_model_call(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            response=missing_response,
            purpose="missing_files_repair",
            settings=settings,
            duration_ms=missing_duration_ms,
        )
        missing_payload = await _extract_json_or_repair(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            responses_dir=responses_dir,
            source_stage="missing_files_repair",
            raw_text=missing_response.text,
            expected_shape='{"files":[{"path":"generated/agent.py","content":"..."}]}',
            settings=settings,
        )
        repair_files = (
            _normalise_files_payload(missing_payload) if isinstance(missing_payload, dict) else None
        )
        if repair_files:
            files_payload.update(repair_files)
        missing = _missing_required_files(sorted(files_payload))
        if missing:
            return ModelAuthoringResult(
                ok=False,
                error_code=_missing_required_files_error(missing),
                message=f"Model did not provide required generated files: {missing}",
                stages=stages_completed + [code_stage, "test_generation"],
            )

    record_model_orchestrated_call(
        event_log=event_log,
        session_id=session_id,
        step=step,
        purpose="static_safety_scan",
        phase="author.build",
        status="started",
        inputs=sorted(files_payload.keys()),
    )
    safety_issues = _safety_issues_for_files(files_payload)
    record_model_orchestrated_call(
        event_log=event_log,
        session_id=session_id,
        step=step,
        purpose="static_safety_scan",
        phase="author.build",
        status="completed" if not safety_issues else "failed",
        inputs=sorted(files_payload.keys()),
        detail=None if not safety_issues else "unsafe constructs detected",
    )
    if safety_issues:
        violation_context = _unsafe_violation_context(
            files=files_payload,
            issues=safety_issues,
        )
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "safety_repair_requested",
                "stage": stage_label,
                "unsafe_findings": safety_issues,
                "unsafe_violation_context": violation_context,
            },
            step=step,
        )
        safety_prompt = _safety_repair_prompt(
            reviewed_contract=reviewed_contract,
            files=files_payload,
            unsafe_findings=safety_issues,
        )
        safety_response, safety_call_error, safety_duration_ms = await _call_model(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            system_prompt="You repair unsafe generated Python. Return JSON only.",
            user_prompt=safety_prompt,
            purpose="safety_repair",
            settings=settings,
        )
        if safety_response is None:
            return ModelAuthoringResult(
                ok=False,
                error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
                message=f"Generated code failed safety checks: {safety_issues}",
                technical_detail="\n".join(
                    part
                    for part in (
                        _safety_failure_detail(
                            issues=safety_issues,
                            files=files_payload,
                            intro="Initial generated code failed safety checks.",
                        ),
                        safety_call_error,
                    )
                    if part
                ),
                stages=stages_completed + [code_stage, "test_generation"],
            )
        _write_response_artifacts(
            responses_dir=responses_dir,
            stage="safety_repair",
            prompt=safety_prompt,
            response_text=safety_response.text,
        )
        _record_model_call(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            response=safety_response,
            purpose="safety_repair",
            settings=settings,
            duration_ms=safety_duration_ms,
        )
        safety_payload = await _extract_json_or_repair(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            responses_dir=responses_dir,
            source_stage="safety_repair",
            raw_text=safety_response.text,
            expected_shape='{"files":[{"path":"generated/agent.py","content":"..."}]}',
            settings=settings,
        )
        safety_files = (
            _normalise_files_payload(safety_payload) if isinstance(safety_payload, dict) else None
        )
        if safety_files is None:
            return ModelAuthoringResult(
                ok=False,
                error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
                message=f"Generated code failed safety checks: {safety_issues}",
                technical_detail=_safety_failure_detail(
                    issues=safety_issues,
                    files=files_payload,
                    intro=(
                        "Safety repair did not return a valid replacement JSON payload. "
                        "The unsafe file was not repaired."
                    ),
                ),
                stages=stages_completed + [code_stage, "test_generation"],
            )
        if safety_files:
            files_payload.update(safety_files)
        safety_issues = _safety_issues_for_files(files_payload)
        if safety_issues:
            return ModelAuthoringResult(
                ok=False,
                error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
                message=f"Generated code failed safety checks: {safety_issues}",
                technical_detail=_safety_failure_detail(
                    issues=safety_issues,
                    files=files_payload,
                    intro=(
                        "Safety repair returned code that still contains forbidden "
                        "constructs after recheck."
                    ),
                ),
                stages=stages_completed + [code_stage, "test_generation"],
            )

    try:
        written_files, material_failure = _write_model_files(
            workspace=workspace,
            files=files_payload,
            reference_hashes=reference_hashes,
            file_provenance=file_provenance,
        )
    except ValueError as exc:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_CODE_GENERATION_FAILED,
            message=str(exc),
            stages=stages_completed + [code_stage, "test_generation"],
        )
    if material_failure:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE,
            message=(
                f"Required file {material_failure} matched the checked-in reference "
                "without a material model modification."
            ),
            stages=stages_completed + [code_stage, "test_generation"],
            contributed_files=written_files,
        )

    contributed_files.extend(path for path in written_files if path not in contributed_files)
    stages_completed.append(code_stage)
    stages_completed.append("test_generation")

    metadata = getattr(model_client, "metadata", {}) or {}
    interim_provenance = author_provenance_from_events(event_log.read_all(session_id))
    summary_rel = _write_authoring_summary(
        workspace=workspace,
        metadata={
            "model_call_count": interim_provenance.get("model_call_count", 0),
            "tokens_used": interim_provenance.get("tokens_used", 0),
            "validation_result": "pending",
            "model_by_stage": interim_provenance.get("model_by_stage", {}),
        },
        model=interim_provenance.get("llm_model") or metadata.get("model"),
        provider=interim_provenance.get("llm_provider") or metadata.get("provider"),
        stages=stages_completed,
        file_provenance=list(file_provenance.values()),
    )
    contributed_files.append(summary_rel)
    contributed_files = sorted(set(contributed_files))

    provenance = _record_authoring_provenance(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        contributed_files=contributed_files,
        stages_completed=stages_completed,
        file_provenance=file_provenance,
        summary_rel=summary_rel,
    )

    return ModelAuthoringResult(
        ok=True,
        output_contract=reviewed_contract,
        contributed_files=contributed_files,
        stages=stages_completed,
        provenance=provenance,
    )


def execute_author_model_stages(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient | None,
    user_description: str,
    schema_profile: dict[str, Any],
    reference_scaffold: str | None,
    output_contract: dict[str, Any] | None = None,
    workspace: Path | None = None,
    reference_scaffold_root: Path | None = None,
) -> tuple[bool, list[str]]:
    """Backward-compatible wrapper around :func:`run_model_authoring_pipeline`."""
    del reference_scaffold
    if workspace is None:
        return False, []
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            model_client=model_client,
            workspace=workspace,
            user_description=user_description,
            schema_profile=schema_profile,
            reference_scaffold_root=reference_scaffold_root,
            workflow_type=str(
                output_contract.get("workflow_type") if output_contract else "custom"
            ),
            output_contract_hint=output_contract,
        ),
    )
    return result.ok, result.stages


def _has_required_test_contribution(contributed_files: list[str]) -> bool:
    return any(path.endswith(_REQUIRED_TEST_SUFFIX) for path in contributed_files)


def enforce_author_completion_gate(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    workspace: Path | None = None,
) -> ErrorCode | None:
    events = event_log.read_all(session_id)
    model_calls = count_author_model_calls(events)
    stages = effective_author_model_stages(events)
    contributed_files = model_contributed_files_from_events(events)

    if model_calls <= 0:
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING.value,
                "message": AUTHOR_MODEL_REQUIRED_MESSAGE,
                "stage": stage_label,
                "model_calls": model_calls,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING

    if "contract_planning" not in stages:
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED.value,
                "message": "Author completion requires a recorded contract_planning stage.",
                "stage": stage_label,
                "model_calls": model_calls,
                "model_stages": stages,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED

    if (
        workspace is not None
        and not (workspace / "generated" / "model_contract_plan.json").is_file()
    ):
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED.value,
                "message": "Contract planning stage did not produce model_contract_plan.json.",
                "stage": stage_label,
                "model_calls": model_calls,
                "model_stages": stages,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED

    if "contract_review" not in stages:
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED.value,
                "message": "Author completion requires a recorded contract_review stage.",
                "stage": stage_label,
                "model_calls": model_calls,
                "model_stages": stages,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED

    if not _REQUIRED_CODE_STAGES.intersection(stages):
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_CODE_GENERATION_FAILED.value,
                "message": "Author completion requires a recorded code_generation/code_adaptation stage.",
                "stage": stage_label,
                "model_calls": model_calls,
                "model_stages": stages,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_CODE_GENERATION_FAILED

    if not _REQUIRED_TEST_STAGES.intersection(stages):
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_TEST_GENERATION_FAILED.value,
                "message": "Author completion requires a recorded test_generation/validation_check_generation stage.",
                "stage": stage_label,
                "model_calls": model_calls,
                "model_stages": stages,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_TEST_GENERATION_FAILED

    if _REQUIRED_AGENT_PATH not in contributed_files or not _has_required_test_contribution(
        contributed_files
    ):
        event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE.value,
                "message": AUTHOR_MODEL_DID_NOT_CONTRIBUTE_MESSAGE,
                "stage": stage_label,
                "model_calls": model_calls,
                "model_contributed_files": contributed_files,
            },
            step=step,
        )
        return ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE

    if workspace is not None:
        required_paths = (
            workspace / "generated" / "model_contract_plan.json",
            workspace / "generated" / "model_contract_review.json",
            workspace / "generated" / "author_output_contract.json",
            workspace / "generated" / "model_code_plan.json",
            workspace / "generated" / "model_responses",
            workspace / "reports" / "model_authoring_summary.md",
            workspace / _REQUIRED_AGENT_PATH,
            workspace / "generated" / "tests" / "test_agent.py",
        )
        missing = [
            str(path.relative_to(workspace))
            for path in required_paths
            if not (path.is_file() or path.is_dir())
        ]
        if missing:
            event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE.value,
                    "message": AUTHOR_MODEL_DID_NOT_CONTRIBUTE_MESSAGE,
                    "stage": stage_label,
                    "missing_artifacts": missing,
                },
                step=step,
            )
            return ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE

        payload = _latest_model_authoring_payload(events)
        for entry in payload.get("file_provenance") or []:
            if not isinstance(entry, dict):
                continue
            rel = entry.get("path")
            final_hash = entry.get("final_hash")
            if not isinstance(rel, str) or not isinstance(final_hash, str):
                continue
            artifact = workspace / rel
            if artifact.is_file() and _sha256_file(artifact) != final_hash:
                event_log.append(
                    session_id=session_id,
                    kind=EventKind.WORKFLOW_FAILED,
                    actor_type=ActorType.SYSTEM,
                    payload={
                        "error_code": ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED.value,
                        "message": (
                            f"Final artifact `{rel}` no longer matches the recorded model hash."
                        ),
                        "stage": stage_label,
                        "path": rel,
                    },
                    step=step,
                )
                return ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED

    return None


def author_provenance_from_events(events: list[Any]) -> dict[str, Any]:
    provider = model = base_url = None
    models_seen: list[str] = []
    model_by_stage: dict[str, str] = {}
    model_call_count = 0
    tokens_used = 0
    for event in events:
        if event.kind != EventKind.MODEL_CALLED:
            continue
        model_call_count += 1
        payload = event.payload or {}
        if provider is None:
            provider = payload.get("provider")
            model = payload.get("model")
            base_url = payload.get("base_url")
        event_model = payload.get("model")
        if isinstance(event_model, str) and event_model:
            if event_model not in models_seen:
                models_seen.append(event_model)
            purpose = payload.get("purpose")
            if isinstance(purpose, str) and purpose:
                model_by_stage[purpose] = event_model
        usage = payload.get("usage") or {}
        total = usage.get("total_tokens")
        if isinstance(total, int):
            tokens_used += total
        else:
            tokens_used += int(usage.get("input_tokens") or 0)
            tokens_used += int(usage.get("output_tokens") or 0)

    contributed_files = model_contributed_files_from_events(events)
    stages = collect_author_model_stages(events)
    if len(models_seen) > 1:
        model = ", ".join(models_seen)
    return {
        "llm_provider": provider,
        "llm_model": model,
        "llm_base_url": base_url,
        "model_call_count": model_call_count,
        "tokens_used": tokens_used,
        "model_contributed": bool(contributed_files),
        "model_stages": stages,
        "model_contributed_files": contributed_files,
        "model_by_stage": model_by_stage,
    }


def repair_candidate_history_from_events(events: list[Any]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    seen_attempts: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.kind != EventKind.DECISION_INPUT:
            continue
        payload = event.payload or {}
        kind = payload.get("kind")
        if kind not in {
            "repair_candidate_staged",
            "repair_candidate_rejected",
            "repair_candidate_promoted",
        }:
            continue
        attempt = payload.get("attempt")
        if not isinstance(attempt, int):
            continue
        current = seen_attempts.setdefault(
            attempt,
            {
                "attempt": attempt,
                "status": "staged",
                "failure_kind": payload.get("failure_kind"),
                "candidate_paths": payload.get("candidate_paths") or [],
                "model": payload.get("model", "unknown"),
            },
        )
        current["candidate_paths"] = payload.get("candidate_paths") or current["candidate_paths"]
        if isinstance(payload.get("model"), str) and payload.get("model"):
            current["model"] = payload["model"]
        if isinstance(payload.get("failure_kind"), str) and payload.get("failure_kind"):
            current["failure_kind"] = payload["failure_kind"]
        if kind == "repair_candidate_rejected":
            current["status"] = "rejected"
            current["syntax_error"] = payload.get("syntax_error")
        elif kind == "repair_candidate_promoted":
            current["status"] = "promoted"
        history = [*seen_attempts.values()]
    return history


BACKEND_POLICY_CONTRACT_CONTRIBUTION = "backend_policy_patch"


def sync_author_output_contract_provenance(
    *,
    session_id: UUID,
    event_log: EventLog,
    workspace: Path,
    contract: AuthorOutputContract,
    step: int,
    stage_label: str,
    patch_reason: str = BACKEND_POLICY_CONTRACT_CONTRIBUTION,
) -> None:
    """Persist the contract with canonical JSON and refresh the recorded file hash."""
    rel = "generated/author_output_contract.json"
    _write_json(workspace / rel, contract.model_dump(mode="json"))
    file_hash = _sha256_file(workspace / rel)

    events = event_log.read_all(session_id)
    latest = _latest_model_authoring_payload(events)
    raw_entries = latest.get("file_provenance")
    entries: list[dict[str, Any]] = []
    if isinstance(raw_entries, list):
        entries = [dict(entry) for entry in raw_entries if isinstance(entry, dict)]

    updated_entry: dict[str, Any] = {
        "path": rel,
        "reference_hash": None,
        "model_output_hash": file_hash,
        "final_hash": file_hash,
        "contribution": patch_reason,
    }
    replaced = False
    for index, entry in enumerate(entries):
        if entry.get("path") != rel:
            continue
        model_hash = entry.get("model_output_hash")
        if isinstance(model_hash, str):
            updated_entry["model_output_hash"] = model_hash
        entries[index] = {**entry, **updated_entry}
        replaced = True
        break
    if not replaced:
        entries.append(updated_entry)

    provenance = author_provenance_from_events(events)
    contributed = list(provenance.get("model_contributed_files") or [])
    if rel not in contributed:
        contributed.append(rel)
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "model_authoring_provenance",
            "stage": stage_label,
            "model_contributed_files": sorted(set(contributed)),
            "model_stages": list(provenance.get("model_stages") or []),
            "file_provenance": entries,
            "summary_path": latest.get("summary_path") or "reports/model_authoring_summary.md",
            "model_by_stage": latest.get("model_by_stage")
            or provenance.get("model_by_stage")
            or {},
            "backend_policy_patch": patch_reason,
        },
        step=step,
    )


def refresh_model_authoring_summary(
    *,
    session_id: UUID,
    event_log: EventLog,
    workspace: Path,
    validation_result: str,
    output_artifacts: list[str],
) -> None:
    """Update the human provenance summary after execution/validation evidence exists."""
    events = event_log.read_all(session_id)
    provenance = author_provenance_from_events(events)
    payload = _latest_model_authoring_payload(events)
    raw_file_provenance = payload.get("file_provenance")
    file_provenance = raw_file_provenance if isinstance(raw_file_provenance, list) else []
    _write_authoring_summary(
        workspace=workspace,
        metadata={
            "model_call_count": provenance.get("model_call_count", 0),
            "tokens_used": provenance.get("tokens_used", 0),
            "validation_result": validation_result,
            "output_artifacts": output_artifacts,
            "model_by_stage": provenance.get("model_by_stage", {}),
            "repair_candidates": repair_candidate_history_from_events(events),
        },
        model=provenance.get("llm_model"),
        provider=provenance.get("llm_provider"),
        stages=list(provenance.get("model_stages") or []),
        file_provenance=file_provenance,
    )


async def repair_generated_author_files(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    stage_label: str,
    model_client: ModelClient | None,
    workspace: Path,
    user_description: str,
    schema_profile: dict[str, Any],
    contract: AuthorOutputContract,
    failure_kind: str,
    failure_detail: str,
    attempt: int,
    template_hint: str | None = None,
    settings: Settings | None = None,
    defer_promotion: bool = False,
) -> ModelAuthoringResult:
    """Ask the model to repair generated files after execution/tests/validation fail."""
    if model_client is None:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING,
            message=AUTHOR_MODEL_REQUIRED_MESSAGE,
        )
    responses_dir = workspace / "generated" / "model_responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    prompt_schema = _compact_schema_profile(schema_profile)
    agent_path = workspace / "generated" / "agent.py"
    test_path = workspace / "generated" / "tests" / "test_agent.py"
    bundled_bank_reference = _is_bundled_bank_reference_sample(
        template_hint=template_hint,
        schema_profile=prompt_schema,
    )
    bundled_bank_reference_mode = (
        _bank_reference_scaffold_enabled(settings) and bundled_bank_reference
    )
    repair_client = _repair_client_for_attempt(
        model_client=model_client,
        settings=settings,
        attempt=attempt,
        failure_kind=failure_kind,
        prefer_planning_model=bundled_bank_reference_mode,
    )
    repair_metadata = getattr(repair_client, "metadata", {}) or {}
    current_files = []
    current_paths = (test_path,) if failure_kind == "pytest" else (agent_path,)
    for path in current_paths:
        if path.is_file():
            current_files.append(
                {
                    "path": str(path.relative_to(workspace)).replace("\\", "/"),
                    "content": path.read_text(encoding="utf-8"),
                }
            )
    failure_lines = failure_detail.splitlines()
    traceback_excerpt = "\n".join(failure_lines[-30:]) if failure_lines else failure_detail
    exception_match = re.search(
        r"(?P<type>[A-Za-z_][\w.]*(?:Error|Exception)):\s*(?P<message>[^\n]+)",
        failure_detail,
    )
    command_match = re.search(r"^command=(.+)$", failure_detail, re.MULTILINE)
    missing_outputs_match = re.search(
        r"^missing_required_outputs=(.+)$",
        failure_detail,
        re.MULTILINE,
    )
    workspace_tree_match = re.search(
        r"workspace_artifact_tree:\n(?P<tree>[\s\S]+)$",
        failure_detail,
    )
    contract_modified_match = re.search(
        r"^contract_input_modified=(true|false|True|False)$",
        failure_detail,
        re.MULTILINE,
    )
    contract_json_valid_match = re.search(
        r"^contract_json_valid_after_execution=(true|false|True|False)$",
        failure_detail,
        re.MULTILINE,
    )
    location_match = re.search(
        r'File "(?P<path>[^"]+generated/agent\.py)", line (?P<line>\d+)(?:, in [^\n]+)?\n(?P<code>[^\n]+)',
        failure_detail,
    )
    runtime_failure_context: dict[str, Any] = {
        "traceback_excerpt": traceback_excerpt[-4000:],
    }
    if command_match:
        runtime_failure_context["production_command"] = command_match.group(1).strip()
    if missing_outputs_match:
        runtime_failure_context["missing_required_outputs"] = [
            part.strip()
            for part in missing_outputs_match.group(1).split(",")
            if part.strip()
        ]
    if workspace_tree_match:
        runtime_failure_context["workspace_artifact_tree"] = workspace_tree_match.group(
            "tree"
        ).strip()
    if contract_modified_match:
        runtime_failure_context["contract_input_modified"] = (
            contract_modified_match.group(1).lower() == "true"
        )
    if contract_json_valid_match:
        runtime_failure_context["contract_json_valid_after_execution"] = (
            contract_json_valid_match.group(1).lower() == "true"
        )
    if exception_match:
        runtime_failure_context["exception_type"] = exception_match.group("type")
        runtime_failure_context["exception_message"] = exception_match.group("message")
    if location_match:
        runtime_failure_context["failing_path"] = location_match.group("path")
        runtime_failure_context["line_number"] = int(location_match.group("line"))
        runtime_failure_context["failing_line"] = location_match.group("code")
    tuple_unpack_context = _tuple_unpack_arity_failure_context(
        failure_detail=failure_detail,
        current_files=current_files,
    )
    if tuple_unpack_context:
        runtime_failure_context["tuple_unpack_arity"] = tuple_unpack_context
    dictwriter_context = _dictwriter_fieldnames_failure_context(
        failure_detail=failure_detail,
        current_files=current_files,
    )
    if dictwriter_context:
        runtime_failure_context["dictwriter_fieldnames"] = dictwriter_context
    pytest_pathing_context = _pytest_workspace_pathing_failure_context(
        failure_detail=failure_detail,
        current_files=current_files,
    )
    if pytest_pathing_context:
        runtime_failure_context["pytest_workspace_pathing"] = pytest_pathing_context
    repair_contract: dict[str, Any] = {
        "response_shape": (
            '{"files":[{"path":"generated/agent.py","content":"complete corrected '
            'Python source"}],"notes":"...","assumptions":[]}'
        ),
        "complete_files_only": True,
        "partial_snippets_allowed": False,
        "markdown_fences_allowed": False,
        "required_files": ["generated/agent.py"],
        "allowed_files": ["generated/agent.py"],
        "preferred_files": ["generated/agent.py"],
        "safety_requirements": [
            "Do not introduce eval(), exec(), compile(), dynamic import, subprocess, os.system, shell calls, network calls, or filesystem access outside the workspace.",
            "If the failure mentions a forbidden construct such as eval(), remove it completely instead of replacing it with another dynamic execution primitive.",
            "The repaired generated/agent.py must pass the same static safety scan before it can be accepted.",
        ],
        "contract_runtime_shape_requirements": _contract_runtime_shape_requirements(),
    }
    if failure_kind == "syntax":
        repair_contract.update(
            {
                "required_files": ["generated/agent.py"],
                "syntax_requirement": (
                    "generated/agent.py content must be a complete Python file that "
                    "parses with ast.parse before any execution is attempted."
                ),
                "repair_goal": (
                    "Fix the syntax error only. Return a complete syntactically valid "
                    "replacement file and preserve the existing CLI and output interface."
                ),
            }
        )
    elif failure_kind == "execution":
        repair_contract["repair_goal"] = (
            "Make the smallest code change needed to fix the runtime exception while "
            "preserving working syntax, CLI arguments, contract-driven outputs, and "
            "the surrounding implementation."
        )
        if is_missing_numeric_coercion_typeerror(failure_detail):
            repair_contract["data_coercion_repair_requirements"] = (
                data_coercion_repair_requirements()
            )
            repair_contract["data_coercion_guidance"] = codegen_data_coercion_guidance()
        if _is_contract_column_list_shape_typeerror(failure_detail):
            repair_contract["contract_column_list_shape_repair_requirements"] = (
                _contract_column_list_shape_repair_requirements()
            )
        if _is_tuple_unpack_arity_valueerror(failure_detail):
            repair_contract["helper_return_arity_repair_requirements"] = (
                _tuple_unpack_arity_repair_requirements(tuple_unpack_context)
            )
        if _is_dictwriter_fieldnames_valueerror(failure_detail):
            repair_contract["dictwriter_fieldnames_repair_requirements"] = (
                _dictwriter_fieldnames_repair_requirements(dictwriter_context)
            )
    elif failure_kind == "missing_artifacts":
        repair_contract["repair_goal"] = (
            "Make the smallest code or CLI-path fix needed so the agent writes every "
            "required output artifact to the exact required path. Preserve the current "
            "business logic where it already works."
        )
        repair_contract["artifact_repair_requirements"] = [
            "Use runtime_failure_context.missing_required_outputs as the exact missing path list.",
            "Use output_contract_summary.required_output_paths as the full required output list.",
            "Use existing_artifact_context.produced_artifact_paths as the produced artifact list.",
            "Modify generated/agent.py so it writes every missing required artifact to the exact required path.",
            "Do not remove outputs from the contract, reinterpret them as optional, or fake success.",
            "If a required exception CSV has zero matching rows, still create it with the required header row.",
            "If a required summary CSV is missing, compute it using the contract's grouping keys (summary_group_keys) and summary metrics (summary_metrics) and write it to the required path.",
            "Do not exit 0 until every required artifact exists.",
        ]
        if _is_contract_column_list_shape_typeerror(failure_detail):
            repair_contract["contract_column_list_shape_repair_requirements"] = (
                _contract_column_list_shape_repair_requirements()
            )
        if _is_tuple_unpack_arity_valueerror(failure_detail):
            repair_contract["helper_return_arity_repair_requirements"] = (
                _tuple_unpack_arity_repair_requirements(tuple_unpack_context)
            )
        if _is_dictwriter_fieldnames_valueerror(failure_detail):
            repair_contract["dictwriter_fieldnames_repair_requirements"] = (
                _dictwriter_fieldnames_repair_requirements(dictwriter_context)
            )
    elif failure_kind == "contract_validation":
        repair_contract.update(
            {
                "repair_goal": (
                    "Fix only the proven contract-validation violation in "
                    "generated/agent.py. Preserve the working CLI, artifact paths, "
                    "existing passing pytest behavior, and surrounding implementation."
                ),
                "validation_repair_requirements": [
                    "Do not modify generated/tests/test_agent.py unless pytest itself failed.",
                    "Keep the explicit CLI and all currently correct output paths unchanged.",
                    "When the contract marks output columns as required and non-null, populate them for every output row.",
                    "Inspect every branch that writes output rows and ensure each required column is assigned on every branch.",
                    "Category, rule_matched, rule_used, and confidence must be assigned together; never set category without rule fields.",
                    "Add a final per-row normalization/fill step before writing output.csv if any branch can leave required columns empty.",
                    "Treat missing keys, None, empty strings, and whitespace-only strings as null/invalid for required non-null output columns.",
                    "Use required_column_null_counts, failing_row_samples, null counts, and required artifact schema as the source of truth for this repair.",
                    "Do not weaken or reinterpret the contract; fix the agent to satisfy it.",
                ],
            }
        )
    elif failure_kind == "pytest":
        repair_contract.update(
            {
                "response_shape": (
                    '{"files":[{"path":"generated/tests/test_agent.py","content":"complete '
                    'corrected pytest source"}],"notes":"...","assumptions":[]}'
                ),
                "required_files": ["generated/tests/test_agent.py"],
                "allowed_files": ["generated/tests/test_agent.py"],
                "preferred_files": ["generated/tests/test_agent.py"],
                "repair_goal": (
                    "Repair the generated pytest file only. Compare the failing "
                    "pytest assertions against the AuthorOutputContract, the current "
                    "generated tests, and the current generated outputs. Fix invalid "
                    "assertions, imports, CLI usage, or CSV/report parsing mistakes "
                    "inside generated/tests/test_agent.py without rewriting "
                    "generated/agent.py. Remove or replace any assertion that is not "
                    "directly supported by the reviewed contract, the user request, "
                    "required_artifacts, or the explicit CLI contract. Treat "
                    "summary metric identifiers and summary_output_files.required_columns "
                    "as internal contract labels unless the contract explicitly "
                    "requires exact report text."
                ),
                "pytest_repair_requirements": [
                    *_generated_test_workspace_path_requirements(),
                    "Return one complete corrected generated/tests/test_agent.py file.",
                    "If pytest collected 0 tests, create real top-level test_* functions; do not return helpers-only files.",
                    "Define at least three pytest-discoverable test_* functions unless the contract requires fewer checks.",
                    "Include every import used by the test file.",
                    "The test file must be self-contained.",
                    "Preserve the explicit CLI interface and current artifact paths.",
                    "Use csv.DictReader or another header-aware parser for CSV assertions.",
                    "Do not treat the CSV header row as data.",
                    "For row-count preservation, compare data rows to data rows.",
                    "Do not require exact literal report wording unless the contract explicitly requires that exact text.",
                    "Use the exact production CLI and prefer sys.executable if you invoke subprocess.",
                    "If you use subprocess, import subprocess explicitly.",
                    "If you use csv.DictReader, import csv explicitly.",
                    "If you use sys.executable, import sys explicitly.",
                    "Before returning, verify that every referenced module has a matching import.",
                    "Preserve working tests that already pass.",
                    "If required_column_null_counts or failing_row_samples show empty required output columns, repair generated/agent.py (not the tests).",
                    "If failing assertions show Category '<name>' missing from report, a category enum mismatch, or obvious keyword rows classified as Other/Uncategorised, repair generated/agent.py classification/report logic rather than weakening the test.",
                    "If the only failing assertion treats rule_matched='No rule matched' (or similar fallback text) as requiring confidence >= 0.80, repair the test to mirror validation_checks.clear_rule_confidence_08: empty/null rule_matched OR confidence >= 0.80.",
                    "For expense exception review pytest failures on test_allowed_enums or test_exception_rules_logic, remove invented rule_used enum sets and exact exception_reason prose equality on production upload rows; assert stable fields and contract exception_rules names instead.",
                    "Do not modify generated/agent.py unless the failing pytest proves an agent contract violation.",
                    "Every assertion must be traceable to the reviewed contract, the explicit CLI contract, the user request, or the required artifact list.",
                    "Treat SummaryMetricSpec names and report required_columns as internal identifiers unless the contract explicitly requires exact literal text.",
                    "For report coverage tied to summary metrics, prefer normalized human-readable headings or data-derived evidence checks over raw snake_case string matches.",
                    "Do not invent domain heuristics or implicit finance assumptions. Do not assert amount sign by category unless the contract explicitly requires that rule.",
                    "Add one short comment above each test naming the contract requirement or artifact it verifies.",
                    "Make the smallest change needed for the failing assertions only.",
                ],
            }
        )
        if _is_pytest_workspace_pathing_failure(failure_detail):
            repair_contract["pytest_workspace_pathing_repair_requirements"] = (
                _pytest_workspace_pathing_repair_requirements(pytest_pathing_context)
            )
        elif _is_expense_exception_pytest_brittle_failure(failure_detail) and (
            _is_expense_exception_review_context(
                template_hint=template_hint,
                schema_profile=prompt_schema,
            )
            or _contract_has_exception_review_outputs(contract)
        ):
            repair_contract["expense_exception_pytest_repair_requirements"] = (
                _expense_exception_pytest_repair_requirements(contract=contract)
            )
    else:
        repair_contract["repair_goal"] = (
            "Make the smallest code change needed to fix the reported failure while "
            "preserving the current CLI and contract-driven output interface."
        )
    if bundled_bank_reference_mode:
        repair_contract["reference_sample_constraints"] = {
            "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
            "warning": _BUNDLED_BANK_REFERENCE_WARNING,
            "required_categories": list(_BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES),
            "expected_rows": list(_BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS),
            "repair_requirements": _bundled_bank_reference_repair_requirements(),
        }
    elif _is_custom_bank_categoriser_contract(
        template_hint=template_hint,
        contract=contract,
    ):
        repair_contract["custom_bank_constraints"] = {
            "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
            "warning": _CUSTOM_BANK_CATEGORISER_WARNING,
            "required_categories": list(_normalise_allowed_categories(contract)),
            "repair_requirements": _custom_bank_categoriser_repair_requirements(
                contract=contract,
                schema_profile=prompt_schema,
            ),
        }
    instruction = (
        "Repair the generated Author agent file. Return strict JSON only with "
        "`files` as a list of complete replacement Python files. Include any "
        "file you modify. Do not return markdown fences, prose, or partial "
        "snippets. Preserve the contract-driven outputs and existing CLI "
        "interface. Make the smallest change needed for this failure and do "
        "not rewrite unrelated working code. Avoid network, subprocess, eval, "
        "exec, environment, or absolute system path access."
    )
    if failure_kind == "pytest":
        instruction = (
            "Repair the generated Author pytest file. Return strict JSON only with "
            "`files` as a list of complete replacement Python files. Include any "
            "file you modify. Do not return markdown fences, prose, or partial "
            "snippets. Preserve the contract-driven outputs and existing CLI "
            "interface. Make the smallest change needed for this failure and do "
            "not rewrite unrelated working code. Do not add network, eval, exec, "
            "environment, or absolute system path access. Subprocess usage is "
            "allowed only for invoking the generated agent through the required "
            "production CLI, and the test file must import every symbol it uses. "
            "If a failing assertion is not contract-backed, remove or replace it "
            "with a contract-backed assertion instead of inventing a finance heuristic. "
            "If a failing report assertion is checking a raw snake_case metric "
            "identifier, convert it to a semantic or normalized heading check unless "
            "the contract explicitly requires exact wording. "
            "If the failure is FileNotFoundError, ImportError, or 'can't open file ... "
            "generated/agent.py' under a pytest temporary directory, repair path "
            "resolution to use the session workspace root before changing business "
            "assertions."
        )
    compact_repair = _use_compact_execution_repair_prompts(
        schema_profile=prompt_schema,
        settings=settings,
        failure_kind=failure_kind,
    )
    if compact_repair and failure_kind == "pytest":
        repair_contract["pytest_repair_requirements"] = _compact_pytest_repair_requirements()
    failure_detail_for_prompt = (
        _compact_repair_failure_detail(failure_detail)
        if compact_repair
        else failure_detail[-6000:]
    )
    output_summary = (
        _compact_output_contract_summary(contract)
        if compact_repair
        else {
            "required_output_paths": contract.all_required_output_paths(),
            "required_artifacts": _required_artifact_specs(contract),
            "optional_output_paths": _optional_artifact_paths(contract),
            "required_output_columns": contract.required_output_columns,
            "required_output_column_semantics": [
                spec.model_dump(mode="json")
                for spec in contract.required_output_column_semantics()
            ],
            "preserve_row_count": contract.preserve_row_count,
        }
    )
    artifact_context = (
        _compact_repair_artifact_context(workspace=workspace, contract=contract)
        if compact_repair
        else _artifact_context_for_repair(workspace=workspace, contract=contract)
    )
    prompt = _json_prompt(
        {
            "stage": "execution_repair",
            "attempt": attempt,
            "failure_kind": failure_kind,
            "failure_detail": failure_detail_for_prompt,
            "runtime_failure_context": runtime_failure_context,
            "repair_contract": repair_contract,
            "user_description": user_description,
            "schema_profile": prompt_schema,
            "author_output_contract": _contract_for_prompt(
                contract,
                compact=compact_repair,
            ),
            "required_cli_interface": _generated_agent_cli_contract(contract),
            "output_contract_summary": output_summary,
            "existing_artifact_context": artifact_context,
            "current_files": current_files,
            "instruction": instruction,
        },
    )
    _record_stage_prompt_metrics(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        purpose="execution_repair",
        prompt=prompt,
        compact_mode=compact_repair,
        extra={"attempt": attempt, "failure_kind": failure_kind},
    )
    response, repair_call_error, repair_duration_ms = await _call_model(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        system_prompt=(
            "You repair generated finance agent code and pytest checks. Return JSON "
            "only in the requested files-list shape."
        ),
        user_prompt=prompt,
        purpose="execution_repair",
        settings=settings,
        client_override=repair_client,
        attempt=attempt,
    )
    if response is None:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
            message="Generated-code repair model call failed.",
            technical_detail=repair_call_error,
        )
    stage = f"execution_repair_{attempt}"
    _write_response_artifacts(
        responses_dir=responses_dir,
        stage=stage,
        prompt=prompt,
        response_text=response.text,
    )
    _record_model_call(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=repair_client,
        response=response,
        purpose="execution_repair",
        settings=None,
        attempt=attempt,
        duration_ms=repair_duration_ms,
    )
    expected_shape = repair_contract["response_shape"]
    payload = await _extract_json_or_repair(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        model_client=model_client,
        responses_dir=responses_dir,
        source_stage=stage,
        raw_text=response.text,
        expected_shape=expected_shape,
        settings=settings,
    )
    files = _normalise_files_payload(payload) if isinstance(payload, dict) else None
    if not files:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
            message="Generated-code repair did not return a strict files list.",
        )
    actual_paths = {path.replace("\\", "/") for path in files}
    allowed_paths = {
        path.replace("\\", "/")
        for path in list(repair_contract.get("allowed_files") or [])
        if isinstance(path, str)
    }
    required_paths = {
        path.replace("\\", "/")
        for path in list(repair_contract.get("required_files") or [])
        if isinstance(path, str)
    }
    if allowed_paths:
        unexpected = sorted(actual_paths - allowed_paths)
        missing_required = sorted(required_paths - actual_paths)
        if unexpected or missing_required:
            return ModelAuthoringResult(
                ok=False,
                error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
                message="Generated-code repair returned the wrong replacement file set.",
                technical_detail=(
                    f"allowed_paths={', '.join(sorted(allowed_paths)) or '(none)'}\n"
                    f"actual_paths={', '.join(sorted(actual_paths)) or '(none)'}\n"
                    f"missing_required={', '.join(missing_required) or '(none)'}\n"
                    f"unexpected_paths={', '.join(unexpected) or '(none)'}"
                ),
            )
    safety_issues = _safety_issues_for_files(files)
    if safety_issues:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
            message=f"Generated-code repair failed safety checks: {safety_issues}",
        )
    try:
        candidate_paths = _stage_repair_candidate_files(
            workspace=workspace,
            files=files,
            attempt=attempt,
        )
    except ValueError as exc:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
            message=str(exc),
        )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "repair_candidate_staged",
            "stage": stage_label,
            "attempt": attempt,
            "failure_kind": failure_kind,
            "candidate_paths": candidate_paths,
            "provider": repair_metadata.get("provider", "unknown"),
            "model": repair_metadata.get("model", "unknown"),
        },
        step=step,
    )
    candidate_syntax_failure = _repair_candidate_syntax_failure(files=files, attempt=attempt)
    if candidate_syntax_failure:
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "repair_candidate_rejected",
                "stage": stage_label,
                "attempt": attempt,
                "failure_kind": "syntax",
                "candidate_paths": candidate_paths,
                "provider": repair_metadata.get("provider", "unknown"),
                "model": repair_metadata.get("model", "unknown"),
                "syntax_error": candidate_syntax_failure,
            },
            step=step,
        )
        refresh_model_authoring_summary(
            session_id=session_id,
            event_log=event_log,
            workspace=workspace,
            validation_result="pending_after_repair",
            output_artifacts=[],
        )
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
            message="Repair candidate failed Python syntax preflight before promotion.",
            technical_detail=candidate_syntax_failure,
            repair_candidate_paths=candidate_paths,
            repair_candidate_files=dict(files),
            repair_provider=repair_metadata.get("provider", "unknown"),
            repair_model=repair_metadata.get("model", "unknown"),
            retryable=True,
            repair_failure_kind="pytest" if failure_kind == "pytest" else "syntax",
        )

    if defer_promotion:
        return ModelAuthoringResult(
            ok=True,
            output_contract=contract,
            repair_candidate_paths=candidate_paths,
            repair_candidate_files=dict(files),
            repair_candidate_promoted=False,
            repair_provider=repair_metadata.get("provider", "unknown"),
            repair_model=repair_metadata.get("model", "unknown"),
        )
    try:
        written, material_failure, file_provenance = promote_repair_candidate_files(
            session_id=session_id,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            workspace=workspace,
            attempt=attempt,
            failure_kind=failure_kind,
            candidate_paths=candidate_paths,
            files=files,
            provider=repair_metadata.get("provider", "unknown"),
            model=repair_metadata.get("model", "unknown"),
        )
    except ValueError as exc:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
            message=str(exc),
        )
    if material_failure:
        return ModelAuthoringResult(
            ok=False,
            error_code=ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE,
            message=(
                f"Repair returned {material_failure} matching the reference without "
                "material modification."
            ),
        )

    contributed_files, stages, provenance = finalize_repair_candidate_promotion(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        written=written,
        file_provenance=file_provenance,
    )
    return ModelAuthoringResult(
        ok=True,
        output_contract=contract,
        contributed_files=contributed_files,
        stages=stages,
        provenance=provenance,
        repair_candidate_paths=candidate_paths,
        repair_candidate_files=dict(files),
        repair_candidate_promoted=True,
        repair_provider=repair_metadata.get("provider", "unknown"),
        repair_model=repair_metadata.get("model", "unknown"),
    )
