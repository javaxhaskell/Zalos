"""Author code-generation reliability — raw Python path, bounded output, timeout detail."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.config import Settings
from agentforge.models import FakeModelClient
from agentforge.models.client import ModelClientError, ModelMessage, ModelResponse, TextBlock
from agentforge.models.ollama_client import OllamaModelClient
from agentforge.orchestrator.author_llm_authoring import (
    _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES,
    _bundled_bank_reference_demo_client_override,
    _codegen_max_tokens_for_purpose,
    _codegen_prompt,
    _detect_helper_return_arity_issues,
    _detect_dictwriter_fieldname_issues,
    _contract_runtime_shape_requirements,
    _bundled_bank_reference_contract_scaffold,
    _classification_test_generation_requirements,
    _custom_bank_categoriser_codegen_section,
    _extract_explicit_prompt_category_labels,
    _is_custom_bank_categoriser_sample,
    _is_contract_column_list_shape_typeerror,
    _is_dictwriter_fieldnames_valueerror,
    _is_pytest_workspace_pathing_failure,
    _is_expense_exception_pytest_brittle_failure,
    _expense_exception_pytest_repair_requirements,
    _is_tuple_unpack_arity_valueerror,
    _normalize_generated_test_workspace_root,
    _pytest_workspace_pathing_failure_context,
    _pytest_workspace_pathing_repair_requirements,
    _stage_repair_candidate_files,
    _workspace_root_parent_index,
    _configured_timeout_seconds,
    _optional_artifact_paths,
    _parse_codegen_agent_source,
    _safety_issues_for_files,
    _safety_repair_prompt,
    _test_generation_prompt,
    _write_codegen_debug_artifacts,
    enforce_author_completion_gate,
    run_model_authoring_pipeline,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, ErrorCode, EventKind, Workflow
from agentforge.schemas.author_output_contract import AuthorOutputContract
from tests.author_model_fixtures import _agent_source, model_authoring_responses
from tests.test_author_completion_gate import _purposes, _run_pipeline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"
_FIXTURE_BANK_DEMO_EXECUTION_REPAIR_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_demo_execution_repair_failure_c8484d54.json"
)
_FIXTURE_BANK_DEMO_TUPLE_ARITY_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_demo_tuple_arity_failure_46efe089.json"
)
_FIXTURE_BANK_DEMO_EXCEPTION_CSV_FIELDNAMES_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_demo_exception_csv_fieldnames_failure_f2f84399.json"
)
_FIXTURE_BANK_GENERATED_PYTEST_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_generated_pytest_failure_5aa804ba.json"
)
_BANK_REFERENCE_USER_DESCRIPTION = (
    "Build context: Bank transaction categorisation reference sample\n\n"
    "Assign each transaction to exactly one of: Revenue, Payroll, Software, "
    "Bank Fees, Travel, Rent, Tax, Office Supplies, Other."
)
_CUSTOM_BANK_USER_DESCRIPTION = (
    "Categorise these bank transactions into Income, Office Expense, Travel, "
    "Subscriptions, Refund, Uncategorised. Use vendor name patterns. Negative "
    "amounts are refunds. Match case-insensitively."
)


def _custom_bank_schema_profile() -> dict[str, object]:
    return {
        "columns": [
            "txn_id",
            "date",
            "amount",
            "description",
            "counterparty",
            "account",
        ],
        "row_count": 10,
        "upload_format": "csv",
        "agent_input_path": "uploads/input.csv",
        "normalized_input_path": "uploads/input.csv",
        "sample_rows": [],
    }


def _custom_bank_contract() -> AuthorOutputContract:
    return AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [],
            "exception_output_files": [],
            "primary_row_key": "txn_id",
            "input_columns": [
                "txn_id",
                "date",
                "amount",
                "description",
                "counterparty",
                "account",
            ],
            "output_columns": [
                "txn_id",
                "date",
                "amount",
                "description",
                "counterparty",
                "account",
                "category",
                "rule_matched",
            ],
            "required_output_columns": ["category", "rule_matched"],
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
            "validation_checks": [],
            "skipped_checks": ["golden_output"],
        }
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


@dataclass
class TimeoutOnCallClient:
    """Replay prefix responses, then raise a timeout-like ModelClientError."""

    prefix: list[ModelResponse]
    fail_on_call: int
    calls: list[dict] = field(default_factory=list)
    _cursor: int = 0

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list,
        max_tokens: int | None = 4096,
    ) -> ModelResponse:
        self.calls.append({"max_tokens": max_tokens})
        self._cursor += 1
        if self._cursor < self.fail_on_call:
            if self._cursor - 1 >= len(self.prefix):
                raise ModelClientError("unexpected extra model call in timeout test")
            return self.prefix[self._cursor - 1]
        raise ModelClientError(
            f"Ollama request timed out after 900.0s (ReadTimeout on purpose={self._cursor})"
        )

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "provider": "ollama",
            "model": "qwen2.5-coder:14b",
            "base_url": "http://localhost:11434",
        }


def _base_script() -> list[ModelResponse]:
    return model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )


def test_parse_codegen_agent_source_accepts_raw_python() -> None:
    source, error = _parse_codegen_agent_source(_agent_source())
    assert error is None
    assert source is not None
    assert "import csv" in source


def test_parse_codegen_agent_source_accepts_legacy_json_files_list() -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": "def main():\n    return 0\n",
                }
            ]
        }
    )
    source, error = _parse_codegen_agent_source(payload)
    assert error is None
    assert source == "def main():\n    return 0\n"


def test_parse_codegen_agent_source_rejects_empty_output() -> None:
    source, error = _parse_codegen_agent_source("   \n")
    assert source is None
    assert error == "empty response"


def test_parse_codegen_agent_source_rejects_malformed_output() -> None:
    source, error = _parse_codegen_agent_source("Here is some prose but no Python.")
    assert source is None
    assert "missing Python source markers" in (error or "")


def test_codegen_timeout_surfaces_author_code_generation_failed_with_timeout_detail(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    client = TimeoutOnCallClient(prefix=script[:2], fail_on_call=3)
    settings = Settings(
        llm_provider="ollama",
        ollama_timeout_seconds=900,
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
            stage_label="test_codegen_timeout",
            model_client=client,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "sample_rows": [],
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
            settings=settings,
        )
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CODE_GENERATION_FAILED
    assert result.technical_detail
    detail = result.technical_detail.lower()
    assert "timeout" in detail
    assert "purpose=code_generation" in result.technical_detail
    assert "configured_timeout_seconds=900" in result.technical_detail
    assert "failure=timeout" in result.technical_detail
    assert not (workspace / "generated" / "agent.py").exists()
    assert not (workspace / "generated" / "tests" / "test_agent.py").exists()


def test_configured_timeout_seconds_uses_global_timeout() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_timeout_seconds=900,
    )
    client = TimeoutOnCallClient(prefix=[], fail_on_call=1)
    assert _configured_timeout_seconds(client, settings, purpose="code_generation") == 900.0
    assert _configured_timeout_seconds(client, settings, purpose="test_generation") == 900.0
    assert _configured_timeout_seconds(client, settings, purpose="contract_planning") == 900.0


def test_codegen_model_call_omits_max_tokens(workspaces_root: Path) -> None:
    script = _base_script()
    client = TimeoutOnCallClient(prefix=script, fail_on_call=999)
    settings = Settings(llm_provider="ollama", ollama_num_ctx=12288)
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
            stage_label="test_codegen_max_tokens",
            model_client=client,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "sample_rows": [],
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
            settings=settings,
        )
    )
    assert result.ok is True
    assert len(client.calls) >= 3
    assert client.calls[2]["max_tokens"] is None


def test_model_called_event_records_configured_max_tokens_for_contract_planning(
    workspaces_root: Path,
) -> None:
    script = _base_script()
    result, event_log, sid, _workspace = _run_pipeline(workspaces_root, script)
    assert result.ok is True
    model_events = [
        event
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    ]
    planning = next(
        event.payload for event in model_events if event.payload.get("purpose") == "contract_planning"
    )
    assert planning["configured_max_tokens"] == 4096
    assert planning["usage"]["output_tokens"] >= 0
    assert planning["hit_configured_max_tokens"] is False
    assert "near_configured_max_tokens" in planning


def test_ollama_model_call_events_record_routed_models(
    workspaces_root: Path,
    monkeypatch,
) -> None:
    script = _base_script()
    calls: list[dict] = []

    async def fake_complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list,
        max_tokens: int | None = 4096,
    ) -> ModelResponse:
        del system_prompt, messages, tools
        calls.append({"model": self.model, "max_tokens": max_tokens})
        if len(calls) > len(script):
            raise ModelClientError("unexpected extra model call")
        return script[len(calls) - 1]

    monkeypatch.setattr(OllamaModelClient, "complete", fake_complete)
    settings = Settings(
        llm_provider="ollama",
        ollama_model="qwen2.5-coder:14b",
        ollama_planning_model="qwen2.5-coder:14b",
        ollama_codegen_model="qwen2.5-coder:7b",
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
            stage_label="test_ollama_routing_telemetry",
            model_client=OllamaModelClient(model="qwen2.5-coder:14b"),
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "sample_rows": [],
            },
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
            settings=settings,
        )
    )

    assert result.ok is True
    by_purpose = {
        event.payload["purpose"]: event.payload["model"]
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    }
    assert by_purpose["contract_planning"] == "qwen2.5-coder:14b"
    assert by_purpose["contract_review"] == "qwen2.5-coder:14b"
    assert by_purpose["code_generation"] == "qwen2.5-coder:7b"
    assert by_purpose["test_generation"] == "qwen2.5-coder:7b"
    assert [call["model"] for call in calls[:4]] == [
        "qwen2.5-coder:14b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:7b",
    ]
    summary = (workspace / "reports" / "model_authoring_summary.md").read_text(
        encoding="utf-8"
    )
    assert "- `contract_planning`: `qwen2.5-coder:14b`" in summary
    assert "- `code_generation`: `qwen2.5-coder:7b`" in summary
    assert "- `test_generation`: `qwen2.5-coder:7b`" in summary


def test_codegen_debug_artifacts_record_prompt_metadata(workspaces_root: Path) -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
        }
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    settings = Settings(llm_provider="ollama", ollama_num_ctx=12288)
    client = OllamaModelClient(model="base-model:14b")
    meta = _write_codegen_debug_artifacts(
        workspace=workspace,
        system_prompt="system",
        user_prompt=prompt,
        reviewed_contract=contract,
        model_client=client,
        settings=settings,
        max_tokens=_codegen_max_tokens_for_purpose("code_generation", settings),
        timeout_seconds=900.0,
    )
    debug_dir = workspace / "generated" / "debug"
    assert (debug_dir / "codegen_prompt.txt").read_text(encoding="utf-8") == prompt
    saved = json.loads((debug_dir / "codegen_prompt_meta.json").read_text(encoding="utf-8"))
    assert saved["prompt_chars"] == meta["prompt_chars"]
    assert saved["prompt_lines"] == meta["prompt_lines"]
    assert saved["output_format"] == "raw_python"
    assert saved["output_format_requested"] == "raw_python"
    assert saved["required_output_file_paths"] == ["outputs/output.csv"]
    assert saved["options_sent_to_ollama"] == {
        "num_ctx": 12288,
        "temperature": 0.1,
    }
    assert saved["num_predict_set"] is False
    assert saved["max_tokens_set"] is False
    assert saved["timeout_seconds"] == 900.0


def test_bundled_bank_reference_codegen_prompt_includes_demo_constraints() -> None:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    prompt = _codegen_prompt(
        workflow_type="bank_transaction_categorisation",
        user_description=_BANK_REFERENCE_USER_DESCRIPTION,
        reviewed_contract=contract,
        template_hint="bank_categoriser",
        enable_bundled_bank_reference_guidance=True,
    )
    assert "Bundled bank reference sample constraints" in prompt
    assert "must always return exactly five values" in prompt
    assert "outputs/output.csv must contain 18 data rows" in prompt
    assert "normalized lowercase haystack" in prompt
    assert "Revenue=3, Payroll=1, Software=4" in prompt
    assert "service charge, bank fee, account fee, barclays" in prompt
    assert "UNKNOWN CARD PURCHASE 9912 -> Other" in prompt


def test_custom_bank_categoriser_detects_non_demo_uploads() -> None:
    assert _is_custom_bank_categoriser_sample(
        template_hint="bank_categoriser",
        schema_profile=_custom_bank_schema_profile(),
    )
    assert not _is_custom_bank_categoriser_sample(
        template_hint="bank_categoriser",
        schema_profile=_bank_reference_schema_profile(),
    )


def test_custom_bank_categoriser_codegen_prompt_includes_keyword_rules() -> None:
    contract = _custom_bank_contract()
    prompt = _codegen_prompt(
        workflow_type="bank_transaction_categorisation",
        user_description=_CUSTOM_BANK_USER_DESCRIPTION,
        reviewed_contract=contract,
        template_hint="bank_categoriser",
        schema_profile=_custom_bank_schema_profile(),
    )
    assert "Custom bank transaction categorisation constraints" in prompt
    assert "Bundled bank reference sample constraints" not in prompt
    assert "Income, Office Expense, Travel, Subscriptions, Refund, Uncategorised" in prompt
    assert "payroll, salary, staff payroll" in prompt
    assert "aws, microsoft, github" in prompt
    assert "Bank Fees: service charge, bank fee, account fee, barclays" in prompt or (
        "Office Expense: service charge, bank fee, account fee, barclays" in prompt
    )
    assert "Tax: hmrc, vat, corporation tax, tax payment" in prompt
    assert "client transfer" in prompt
    assert "Revenue or client receipts -> Income" in prompt
    assert "Software, SaaS, or cloud vendors -> Subscriptions" in prompt


def test_custom_bank_categoriser_test_prompt_avoids_demo_row_assertions() -> None:
    contract = _custom_bank_contract()
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="bank_transaction_categorisation",
            user_description=_CUSTOM_BANK_USER_DESCRIPTION,
            reviewed_contract=contract,
            template_hint="bank_categoriser",
            schema_profile=_custom_bank_schema_profile(),
        )
    )
    requirements = payload["test_quality_requirements"]
    assert any("custom bank-transaction categorisation upload" in item for item in requirements)
    assert any("exactly 10 data rows" in item for item in requirements)
    assert not any("BTX-0001" in item for item in requirements)
    assert not any("Revenue=3, Payroll=1" in item for item in requirements)


def test_bundled_bank_reference_test_prompt_includes_exact_row_expectations() -> None:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="bank_transaction_categorisation",
            user_description=_BANK_REFERENCE_USER_DESCRIPTION,
            reviewed_contract=contract,
            template_hint="bank_categoriser",
            enable_bundled_bank_reference_guidance=True,
        )
    )
    requirements = payload["test_quality_requirements"]
    assert any("exactly 18 data rows" in item for item in requirements)
    assert any("Revenue=3, Payroll=1, Software=4" in item for item in requirements)
    assert any("BTX-0001" in item and "Revenue" in item for item in requirements)
    assert any("BTX-0018" in item and "Other" in item for item in requirements)
    assert payload["author_output_contract"]["allowed_enums"]["category"] == list(
        _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES
    )


def test_bundled_bank_reference_contract_scaffold_skips_stochastic_planning(
    workspaces_root: Path,
) -> None:
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
            stage_label="test_bank_reference_scaffold",
            model_client=FakeModelClient(script=[]),
            workspace=workspace,
            user_description=_BANK_REFERENCE_USER_DESCRIPTION,
            schema_profile=_bank_reference_schema_profile(),
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
            template_hint="bank_categoriser",
            settings=Settings(author_enable_bank_reference_scaffold=True),
        )
    )

    assert result.ok is False
    assert result.error_code in {
        ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED,
        ErrorCode.AUTHOR_CODE_GENERATION_FAILED,
    }
    purposes = [
        event.payload["purpose"]
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    ]
    assert "contract_planning" not in purposes
    scaffold_events = [
        event.payload
        for event in event_log.read_all(sid)
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "reference_sample_contract_scaffold_used"
    ]
    assert any(event["stage"] == "contract_planning" for event in scaffold_events)


def test_bundled_bank_reference_uses_planning_model_for_codegen_override() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_planning_model="qwen2.5-coder:14b",
        ollama_codegen_model="qwen2.5-coder:7b",
        ollama_base_url="http://localhost:11434",
    )
    base_client = OllamaModelClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_codegen_model,
        timeout_seconds=900,
        num_ctx=settings.ollama_num_ctx,
        temperature=settings.ollama_codegen_temperature,
    )

    override = _bundled_bank_reference_demo_client_override(
        model_client=base_client,
        settings=settings,
        purpose="code_generation",
        enabled=True,
    )

    assert isinstance(override, OllamaModelClient)
    assert override.model == settings.ollama_planning_model


def test_required_output_paths_follow_requested_deliverables_when_present() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report",
                    "required_columns": ["category_counts"],
                }
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["row_id", "issue_flag"],
                }
            ],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                },
            ],
        }
    )
    assert contract.all_declared_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
        "outputs/exceptions.csv",
    ]
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]


def test_required_output_paths_fallback_to_declared_outputs_without_requested_deliverables() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report",
                    "required_columns": ["category_counts"],
                }
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["row_id", "issue_flag"],
                }
            ],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
        }
    )
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
        "outputs/exceptions.csv",
    ]


def test_codegen_prompt_highlights_required_artifacts_without_promoting_optional_extras() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report",
                    "required_columns": ["category_counts", "uncertain_rows"],
                }
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["row_id", "issue_flag"],
                }
            ],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                },
            ],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    preamble = prompt.split("User request:", 1)[0]
    assert "Required artifacts:" in preamble
    assert "outputs/output.csv: CSV with header columns: a, flag." in preamble
    assert "reports/validation_report.md: non-empty markdown report; must cover: category_counts, uncertain_rows." in preamble
    required_section = preamble.split("Declared but optional artifacts:", 1)[0]
    assert "outputs/exceptions.csv" not in required_section
    assert "Declared but optional artifacts: outputs/exceptions.csv." in preamble
    assert "Never read, delete, or write a generated output or exception column" in prompt


def test_test_generation_prompt_uses_required_artifacts_instead_of_optional_exception_outputs() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report",
                    "required_columns": ["category_counts"],
                }
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["row_id", "issue_flag"],
                }
            ],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                },
            ],
        }
    )
    prompt = json.loads(
        _test_generation_prompt(
            workflow_type="demo",
            user_description="Review uploaded CSV.",
            reviewed_contract=contract,
        )
    )
    assert [artifact["path"] for artifact in prompt["required_artifacts"]] == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    assert any(
        "Assert only required artifacts listed in required_artifacts" in item
        for item in prompt["test_quality_requirements"]
    )


def test_codegen_prompt_includes_row_completeness_invariant() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["description"],
            "output_columns": ["description", "category", "rule_matched", "rule_used", "confidence"],
            "required_output_columns": [
                "category",
                "rule_matched",
                "rule_used",
                "confidence",
            ],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Categorise rows.",
        reviewed_contract=contract,
    )
    assert "Row-completeness invariant (every output row):" in prompt
    assert "must be populated together on every row" in prompt
    assert "final per-row normalization pass" in prompt


def test_codegen_prompt_requires_exception_csv_when_explicitly_requested() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["row_id", "issue_flag"],
                }
            ],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "exceptions",
                    "output_path": "outputs/exceptions.csv",
                    "required": True,
                },
            ],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    preamble = prompt.split("User request:", 1)[0]
    assert "outputs/exceptions.csv: CSV with header columns: row_id, issue_flag." in preamble
    assert "Create this file even when there are zero matching rows; write the header row." in preamble


def test_codegen_malformed_output_fails_with_technical_detail(workspaces_root: Path) -> None:
    script = _base_script()
    malformed_codegen = ModelResponse(
        id="malformed-codegen",
        content=[TextBlock(text="not python and not json")],
        stop_reason="end_turn",
    )
    result, _event_log, _sid, workspace = _run_pipeline(
        workspaces_root,
        [script[0], script[1], malformed_codegen],
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CODE_GENERATION_FAILED
    assert result.technical_detail
    assert "failure=malformed_agent_source" in result.technical_detail
    assert not (workspace / "generated" / "agent.py").exists()


def test_completion_gate_rejects_missing_generated_agent(workspaces_root: Path) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    for rel in (
        "model_contract_plan.json",
        "model_contract_review.json",
        "author_output_contract.json",
        "model_code_plan.json",
    ):
        (generated / rel).write_text("{}", encoding="utf-8")
    (generated / "tests").mkdir(parents=True, exist_ok=True)
    (generated / "tests" / "test_agent.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / "reports" / "model_authoring_summary.md").write_text(
        "# summary\n", encoding="utf-8"
    )
    (generated / "model_responses").mkdir(parents=True, exist_ok=True)

    for purpose in ("contract_planning", "contract_review", "code_generation", "test_generation"):
        event_log.append(
            session_id=sid,
            kind=EventKind.MODEL_CALLED,
            actor_type=ActorType.MODEL,
            payload={"purpose": purpose, "usage": {"total_tokens": 1}},
            step=1,
        )

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_missing_agent",
        workspace=workspace,
    )

    assert error == ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE


def test_completion_gate_rejects_missing_generated_tests(workspaces_root: Path) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    for rel in (
        "model_contract_plan.json",
        "model_contract_review.json",
        "author_output_contract.json",
        "model_code_plan.json",
        "agent.py",
    ):
        (generated / rel).write_text("{}", encoding="utf-8")
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / "reports" / "model_authoring_summary.md").write_text(
        "# summary\n", encoding="utf-8"
    )
    (generated / "model_responses").mkdir(parents=True, exist_ok=True)

    for purpose in ("contract_planning", "contract_review", "code_generation"):
        event_log.append(
            session_id=sid,
            kind=EventKind.MODEL_CALLED,
            actor_type=ActorType.MODEL,
            payload={"purpose": purpose, "usage": {"total_tokens": 1}},
            step=1,
        )

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_missing_tests",
        workspace=workspace,
    )

    assert error == ErrorCode.AUTHOR_TEST_GENERATION_FAILED


def test_staged_codegen_records_separate_stages_and_provenance(workspaces_root: Path) -> None:
    result, event_log, sid, workspace = _run_pipeline(workspaces_root, _base_script())

    assert result.ok is True
    purposes = _purposes(event_log, sid)
    assert "code_generation" in purposes
    assert "test_generation" in purposes
    assert (workspace / "generated" / "agent.py").is_file()
    assert (workspace / "generated" / "tests" / "test_agent.py").is_file()
    assert (
        workspace / "generated" / "model_responses" / "generated__agent.py.patch.json"
    ).is_file()
    assert (
        workspace / "generated" / "model_responses" / "generated__tests__test_agent.py.patch.json"
    ).is_file()

    gate_error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_staged_provenance",
        workspace=workspace,
    )
    assert gate_error is None


def test_codegen_prompt_requests_raw_python_only_and_excludes_tests() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    lowered = prompt.lower()
    assert "raw python" in lowered
    assert "generated/agent.py" in prompt
    assert "test_agent.py" not in prompt
    assert "required_response_shape" not in prompt
    assert "AuthorOutputContract" in prompt


def test_codegen_and_test_prompts_include_required_cli_interface() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["reports/validation_report.md"],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
        }
    )
    code_prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    test_prompt = _test_generation_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    assert "--input uploads/input.csv" in code_prompt
    assert "--contract generated/author_output_contract.json" in code_prompt
    assert "--row-output outputs/output.csv" in code_prompt
    assert "--report-path reports/validation_report.md" in code_prompt
    assert "Never write CSV rows" in code_prompt

    payload = json.loads(test_prompt)
    cli = payload["required_cli_interface"]
    assert cli["arguments"]["--input"] == "uploads/input.csv"
    assert cli["arguments"]["--contract"] == "generated/author_output_contract.json"
    assert cli["arguments"]["--row-output"] == "outputs/output.csv"
    assert cli["arguments"]["--report-path"] == "reports/validation_report.md"
    assert "same CLI argument shape as production execution" in payload["instruction"]
    assert any(
        "fully self-contained" in item.lower()
        and "import every module and symbol" in item.lower()
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "sys.executable import sys" in item
        and "every referenced module has a matching import" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "csv.DictReader" in item and "Never treat the header row as data" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "invented literal phrase" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "Every assertion must be traceable" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "Do not assert amount sign by category unless the contract explicitly requires that rule."
        in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "internal identifiers" in item and "raw snake_case metric names" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "normalized human-readable labels" in item
        and "category_counts -> Category Counts" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "Only turn validation_checks with required=true into failing pytest assertions."
        in item
        for item in payload["test_quality_requirements"]
    )
    assert "contract trace comment" in payload["instruction"]
    assert "explicit machine-checkable business rule with required=true" in payload["instruction"]
    assert "literal expected sentence" in payload["instruction"]
    assert "internal contract labels unless exact text is explicitly required" in payload["instruction"]
    assert "raw snake_case string matches" in payload["instruction"]


def test_required_report_path_prefers_required_deliverable_over_first_summary_output() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["reports/summary_report.md"],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                },
            ],
        }
    )
    code_prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="demo",
            user_description="Review uploaded CSV.",
            reviewed_contract=contract,
        )
    )

    assert "--report-path reports/validation_report.md" in code_prompt
    assert "--report-path reports/summary_report.md" not in code_prompt
    assert payload["required_cli_interface"]["arguments"]["--report-path"] == (
        "reports/validation_report.md"
    )
    assert [artifact["path"] for artifact in payload["required_artifacts"]] == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    assert payload["optional_artifact_paths"] == ["reports/summary_report.md"]


def test_codegen_and_test_prompts_require_non_null_required_columns() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["reports/validation_report.md"],
            "input_columns": ["a"],
            "output_columns": ["a", "flag", "reason"],
            "required_output_columns": ["flag", "reason"],
            "output_column_semantics": [
                {
                    "name": "flag",
                    "description": "Row-level review flag",
                    "producer_kind": "exception_flag",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty review flag for every row.",
                    "fallback_value_semantics": "Use an explicit default flag when no rule matches.",
                },
                {
                    "name": "reason",
                    "description": "Rule explanation for the row",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule explanation for every row.",
                    "fallback_value_semantics": "Use an explicit default explanation when no rule matches.",
                },
            ],
        }
    )
    code_prompt = _codegen_prompt(
        workflow_type="demo",
        user_description="Review uploaded CSV.",
        reviewed_contract=contract,
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="demo",
            user_description="Review uploaded CSV.",
            reviewed_contract=contract,
        )
    )

    assert "Populate every required output column for every row" in code_prompt
    assert "never leave them blank or null: flag, reason." in code_prompt
    assert "Row semantics: Emit a non-empty review flag for every row." in code_prompt
    assert "Fallback semantics: Use an explicit default explanation when no rule matches." in code_prompt
    assert [item["name"] for item in payload["required_output_column_semantics"]] == [
        "flag",
        "reason",
    ]
    assert any(
        "required non-null output column contains a non-empty value for every data row"
        in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "empty strings, and whitespace-only strings" in item
        for item in payload["test_quality_requirements"]
    )


def test_plain_string_deliverables_are_not_required_completion_gates() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/sample_input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "outputs/summary_by_category.csv",
                    "description": "Summary of transaction counts by category",
                    "required_columns": ["category", "count"],
                }
            ],
            "exception_output_files": [
                {
                    "path": "outputs/review_rows.csv",
                    "description": "Rows that require human review",
                    "required_columns": ["txn_id", "reason_for_review"],
                }
            ],
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "category"],
            "required_output_columns": ["category"],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                },
                "outputs/summary_by_category.csv",
                "outputs/review_rows.csv",
            ],
        }
    )
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    assert set(_optional_artifact_paths(contract)) == {
        "outputs/summary_by_category.csv",
        "outputs/review_rows.csv",
    }


def test_bank_session_contract_derives_only_user_requested_required_files() -> None:
    session_contract = json.loads(
        Path(
            "/Users/arhamshuaib/Desktop/Zalos/.workspaces/"
            "a3706ab2-444b-4958-9357-8c735e8ac7cb/generated/author_output_contract.json"
        ).read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(session_contract)
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]


def test_codegen_prompt_marks_report_derived_csvs_optional_for_bank_contract() -> None:
    session_contract = json.loads(
        Path(
            "/Users/arhamshuaib/Desktop/Zalos/.workspaces/"
            "a3706ab2-444b-4958-9357-8c735e8ac7cb/generated/author_output_contract.json"
        ).read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(session_contract)
    prompt = _codegen_prompt(
        workflow_type="bank_transaction_categorisation",
        user_description="Bank categorisation prompt.",
        reviewed_contract=contract,
    )
    required_block = prompt.split("Report section requirements", 1)[0]
    assert "outputs/summary_by_category.csv" not in required_block.split(
        "Declared but optional artifacts:", 1
    )[0]
    assert "Declared but optional artifacts:" in prompt
    assert "outputs/summary_by_category.csv" in prompt
    assert "outputs/review_rows.csv" in prompt
    assert "Report section requirements" in prompt
    assert "category_counts" in prompt


def test_required_deliverable_discipline_rejects_unjustified_summary_csv() -> None:
    from agentforge.orchestrator.author_llm_authoring import (
        ContractSemanticValidationError,
        _validate_contract_payload_strict,
    )

    payload = {
        "workflow_type": "demo",
        "build_mode": "llm_custom",
        "input_file": "uploads/input.csv",
        "input_format": "csv",
        "row_level_output_file": "outputs/output.csv",
        "summary_output_files": [
            {
                "path": "outputs/summary_by_category.csv",
                "description": "Category counts",
                "required_columns": ["category", "count"],
            }
        ],
        "input_columns": ["a"],
        "output_columns": ["a", "flag"],
        "required_output_columns": ["flag"],
        "output_column_semantics": [
            {
                "name": "flag",
                "description": "Flag",
                "producer_kind": "classification",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Set a flag.",
                "fallback_value_semantics": "Use default.",
            }
        ],
        "requested_deliverables": [
            "outputs/output.csv",
            {
                "name": "summary_by_category",
                "output_path": "outputs/summary_by_category.csv",
                "required": True,
            },
        ],
    }
    with pytest.raises(ContractSemanticValidationError):
        _validate_contract_payload_strict(payload)


def test_required_deliverable_discipline_allows_user_explicit_summary_csv() -> None:
    from agentforge.orchestrator.author_llm_authoring import _validate_contract_payload_strict

    contract = _validate_contract_payload_strict(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "outputs/summary_by_category.csv",
                    "description": "Category counts",
                    "required_columns": ["category", "count"],
                }
            ],
            "input_columns": ["a"],
            "output_columns": ["a", "flag"],
            "required_output_columns": ["flag"],
            "output_column_semantics": [
                {
                    "name": "flag",
                    "description": "Flag",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Set a flag.",
                    "fallback_value_semantics": "Use default.",
                }
            ],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "summary_by_category",
                    "output_path": "outputs/summary_by_category.csv",
                    "required": True,
                    "source": "user_explicit",
                },
            ],
        }
    )
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "outputs/summary_by_category.csv",
    ]


_FIXTURE_6E2CC564_STRING_ARITHMETIC = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/ar_execution_string_arithmetic_6e2cc564.json"
)
_FIXTURE_22C0C771_EVAL_SAFETY_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/vendor_payment_eval_safety_failure_22c0c771.json"
)
_FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/vendor_payment_missing_required_artifacts.json"
)
_FIXTURE_FAFD3422_PYTEST_PATHING = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/expense_generated_pytest_pathing_failure_fafd3422.json"
)
_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/expense_generated_pytest_failure_after_golden_pass_9980bf32.json"
)
_FIXTURE_EED97570_MULTI_RULE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/expense_multi_rule_enum_exception_consistency_eed97570.json"
)
_FIXTURE_3BB42792_EVENT_LOG_CORRUPTION = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/expense_generated_agent_event_log_corruption_3bb42792.json"
)


def test_session_6e2cc564_string_arithmetic_pattern_raises_without_coercion() -> None:
    if not _FIXTURE_6E2CC564_STRING_ARITHMETIC.is_file():
        pytest.skip("6e2cc564 generated-agent fixture unavailable")
    fixture = json.loads(_FIXTURE_6E2CC564_STRING_ARITHMETIC.read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    exec(fixture["calculate_residual_balance_source"], namespace)
    calculate_residual_balance = namespace["calculate_residual_balance"]
    with pytest.raises(TypeError, match="unsupported operand type"):
        calculate_residual_balance(fixture["row"])


def test_vendor_payment_fixture_reproduces_missing_required_artifacts_without_backend_fallback() -> None:
    if not _FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS.is_file():
        pytest.skip("vendor payment missing-artifacts fixture unavailable")
    fixture = json.loads(
        _FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS.read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(fixture["author_output_contract"])
    required_paths = contract.all_required_output_paths()
    assert required_paths == fixture["contract_required_output_paths"]
    produced_paths = set(fixture["produced_artifact_paths"])
    missing_paths = [path for path in required_paths if path not in produced_paths]
    assert missing_paths == fixture["missing_required_output_paths"]
    assert fixture["generated_agent_behavior"]["exit_code"] == 0
    assert "reports/validation_report.md" in produced_paths
    assert "reports/validation_report.md" not in required_paths


def test_vendor_payment_fixture_22c0c771_reproduces_eval_safety_failure() -> None:
    fixture = json.loads(_FIXTURE_22C0C771_EVAL_SAFETY_FAILURE.read_text(encoding="utf-8"))
    issues = _safety_issues_for_files(
        {"generated/agent.py": fixture["generated_agent_source"]}
    )
    assert issues == fixture["unsafe_findings"]
    repaired_issues = _safety_issues_for_files(
        {"generated/agent.py": fixture["safety_repair_candidate_source"]}
    )
    assert repaired_issues == fixture["unsafe_findings"]


def test_codegen_prompt_requires_numeric_coercion_before_arithmetic() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "finance_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["invoice_amount", "paid_amount"],
            "output_columns": ["invoice_amount", "paid_amount", "residual_balance"],
            "required_output_columns": ["residual_balance"],
            "output_column_semantics": [
                {
                    "name": "residual_balance",
                    "description": "Residual balance",
                    "producer_kind": "calculation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Compute residual balance from numeric input fields.",
                    "fallback_value_semantics": "Use zero when allowed by contract semantics.",
                }
            ],
            "calculated_fields": [
                {
                    "name": "residual_balance",
                    "description": "Residual balance",
                    "formula": "invoice_amount - paid_amount",
                }
            ],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="finance_exception_review",
        user_description="Prepare finance review output.",
        reviewed_contract=contract,
    )
    assert "Never perform arithmetic directly on raw CSV/XLSX cell values" in prompt
    assert "safe_decimal" in prompt


def test_codegen_prompt_forbids_eval_exec_compile_subprocess_and_os_system() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "finance_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
        }
    )
    prompt = _codegen_prompt(
        workflow_type="finance_exception_review",
        user_description="Prepare finance review output.",
        reviewed_contract=contract,
    )
    assert "Never use eval(), exec(), compile(), dynamic import, subprocess, os.system, shell calls, or network calls." in prompt
    assert "Never parse formulas, filters, conditions, or business rules with eval" in prompt
    assert "pass the backend static safety scan before execution" in prompt


def test_bank_demo_fixture_reproduces_contract_column_list_shape_failure() -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_EXECUTION_REPAIR_FAILURE.read_text(encoding="utf-8")
    )
    contract = fixture["author_output_contract"]

    with pytest.raises(TypeError, match="string indices"):
        [col["name"] for col in contract["output_columns"]]

    assert _is_contract_column_list_shape_typeerror(fixture["execution_error"]) is True


def test_codegen_prompt_explains_contract_column_lists_are_strings() -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_EXECUTION_REPAIR_FAILURE.read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(fixture["author_output_contract"])
    prompt = _codegen_prompt(
        workflow_type=contract.workflow_type,
        user_description=fixture["user_description"],
        reviewed_contract=contract,
    )
    assert "AuthorOutputContract.output_columns is a list of strings" in prompt
    assert "never use col['name'] for entries from output_columns" in prompt
    assert "AuthorOutputContract.required_output_columns is a list of strings" in prompt
    assert (
        "preserve original input columns first, then add contract output_columns"
        in prompt
    )
    assert "write readable markdown/text evidence, not a CSV file disguised" in prompt
    assert "emit a value strictly below that threshold, not equal to it" in prompt


def test_contract_runtime_shape_requirements_are_available_to_repair() -> None:
    requirements = _contract_runtime_shape_requirements()
    assert any("output_columns is a list of strings" in item for item in requirements)
    assert any("row-level CSV fieldnames" in item for item in requirements)
    assert any("return statement in that helper must return the same number of values" in item for item in requirements)
    assert any("return it twice rather than omitting one" in item for item in requirements)
    assert any("fieldnames must cover every key" in item for item in requirements)
    assert any("Do not pass full output-row dictionaries to a DictWriter" in item for item in requirements)


def test_bank_demo_fixture_reproduces_exception_csv_fieldnames_failure() -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_EXCEPTION_CSV_FIELDNAMES_FAILURE.read_text(encoding="utf-8")
    )
    namespace: dict[str, object] = {}
    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        exec(fixture["generated_agent_source"], namespace)
        namespace["main"]()

    assert _is_dictwriter_fieldnames_valueerror(fixture["execution_error"]) is True
    issues = _detect_dictwriter_fieldname_issues(fixture["generated_agent_source"])
    assert len(issues) == 1
    assert issues[0]["fieldnames"] == fixture["writer_fieldnames"]
    assert issues[0]["writer_method"] == "writerows"
    assert issues[0]["rows_variable"] == "exception_rows"


def test_codegen_prompt_contains_dictwriter_fieldnames_guidance() -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_EXCEPTION_CSV_FIELDNAMES_FAILURE.read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/bank_transaction_categorisation_demo.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": [
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
            "output_columns": [
                "transaction_id",
                "date",
                "account",
                "description",
                "counterparty",
                "amount",
                "currency",
                "reference",
                "direction",
                "category",
                "rule_matched",
                "rule_used",
                "confidence_score",
                "review_required",
            ],
            "required_output_columns": [
                "category",
                "rule_matched",
                "rule_used",
                "confidence_score",
                "review_required",
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "required_columns": fixture["writer_fieldnames"],
                }
            ],
        }
    )
    prompt = _codegen_prompt(
        workflow_type=contract.workflow_type,
        user_description=fixture["user_description"],
        reviewed_contract=contract,
    )
    assert "fieldnames must cover every key" in prompt
    assert "Do not pass full output-row dictionaries to a DictWriter" in prompt
    assert "still write the header row" in prompt


def test_bank_demo_fixture_reproduces_tuple_unpack_arity_failure() -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_TUPLE_ARITY_FAILURE.read_text(encoding="utf-8")
    )
    namespace: dict[str, object] = {}
    exec(fixture["generated_agent_source"], namespace)

    with pytest.raises(ValueError, match="not enough values to unpack"):
        namespace["process_transaction"](
            {"description": "STRIPE PAYOUT MAY 01", "counterparty": "Stripe"}
        )

    assert _is_tuple_unpack_arity_valueerror(fixture["execution_error"]) is True
    issues = _detect_helper_return_arity_issues(fixture["generated_agent_source"])
    assert len(issues) == 1
    assert issues[0]["helper_name"] == "categorize_transaction"
    assert issues[0]["assignment_target_count"] == 5
    assert issues[0]["observed_return_arities"] == [4]


def test_extract_explicit_prompt_category_labels_reads_closed_list() -> None:
    labels = _extract_explicit_prompt_category_labels(
        "Assign each transaction to exactly one of:\n"
        "Revenue, Payroll, Software, Bank Fees, Travel, Rent, Tax, Office Supplies, Other.\n\n"
        "Use these categorisation rules:\n"
        "- Revenue for client transfers."
    )
    assert labels == [
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


def test_codegen_prompt_requires_date_coercion_before_aging() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "finance_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "required_output_columns": ["aging_bucket"],
            "output_column_semantics": [
                {
                    "name": "aging_bucket",
                    "description": "Aging bucket",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Assign aging bucket from due date.",
                    "fallback_value_semantics": "Use fallback bucket when due date is missing.",
                }
            ],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="finance_exception_review",
        user_description="Assign aging buckets as of 2026-05-01.",
        reviewed_contract=contract,
    )
    assert "parse_date" in prompt
    assert "aging buckets or date comparisons" in prompt


def test_execution_repair_recognizes_string_arithmetic_typeerror() -> None:
    from agentforge.orchestrator.generated_agent_coercion import (
        codegen_data_coercion_guidance,
        data_coercion_repair_requirements,
        is_missing_numeric_coercion_typeerror,
    )

    failure = (
        "Generated agent exited 1.\n"
        "stderr: TypeError: unsupported operand type(s) for -: 'str' and 'str'"
    )
    assert is_missing_numeric_coercion_typeerror(failure) is True
    guidance = codegen_data_coercion_guidance()
    assert "safe_decimal" in guidance["repair_rule"]
    assert "parse_date" in guidance["scaffold"]
    assert any("safe_decimal" in item for item in data_coercion_repair_requirements())


def test_test_generation_prompt_includes_string_numeric_input_guidance() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "finance_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
        }
    )
    prompt = _test_generation_prompt(
        workflow_type="finance_exception_review",
        user_description="Prepare finance review output.",
        reviewed_contract=contract,
    )
    assert "string-typed numeric input fixtures" in prompt


def test_safety_repair_prompt_includes_exact_eval_violation_path_and_snippet() -> None:
    fixture = json.loads(_FIXTURE_22C0C771_EVAL_SAFETY_FAILURE.read_text(encoding="utf-8"))
    contract = AuthorOutputContract.model_validate(fixture["author_output_contract"])
    prompt = json.loads(
        _safety_repair_prompt(
            reviewed_contract=contract,
            files={"generated/agent.py": fixture["generated_agent_source"]},
            unsafe_findings=fixture["unsafe_findings"],
        )
    )
    assert prompt["unsafe_findings"] == {"generated/agent.py": ["eval()"]}
    assert prompt["files"] == [
        {
            "path": "generated/agent.py",
            "content": fixture["generated_agent_source"],
        }
    ]
    context = prompt["unsafe_violation_context"][0]
    assert context["path"] == "generated/agent.py"
    assert context["construct"] == "eval()"
    assert "eval(" in context["snippet"]
    assert "eval()" in prompt["forbidden_constructs"]
    assert "exec()" in prompt["forbidden_constructs"]
    assert "compile()" in prompt["forbidden_constructs"]
    assert "generated/agent.py" in prompt["instruction"]
    assert "Remove each forbidden construct completely." in prompt["instruction"]
    assert "The same safety scanner will run again after repair." in prompt["instruction"]
    assert "If eval() remains anywhere in generated/agent.py, the patch is invalid." in prompt["instruction"]


def test_codegen_prompt_requires_all_required_artifacts_not_just_row_output() -> None:
    fixture = json.loads(
        _FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS.read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(fixture["author_output_contract"])
    prompt = _codegen_prompt(
        workflow_type=contract.workflow_type,
        user_description="Prepare vendor payments for finance review.",
        reviewed_contract=contract,
    )
    assert "Inspect the contract for every required output path before you return success." in prompt
    assert (
        "Do not stop after writing only the row-level output CSV when the contract also requires summary or exception artifacts."
        in prompt
    )
    assert (
        "If the contract requires an exception CSV, write it to the exact required path even when no rows match; include the required header row."
        in prompt
    )
    assert (
        "Compute and write this required summary using the contract's summary_group_keys and summary_metrics; do not skip it just because the row-level output exists."
        in prompt
    )
    assert "Summary metrics to write: payable_amount." in prompt
    assert (
        "The row-level output alone is not sufficient when other required artifacts are listed above."
        in prompt
    )


def test_test_generation_prompt_requires_all_required_artifacts_to_exist_and_be_readable() -> None:
    fixture = json.loads(
        _FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS.read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(fixture["author_output_contract"])
    payload = json.loads(
        _test_generation_prompt(
            workflow_type=contract.workflow_type,
            user_description="Prepare vendor payments for finance review.",
            reviewed_contract=contract,
        )
    )
    assert [artifact["path"] for artifact in payload["required_artifacts"]] == [
        "outputs/output.csv",
        "outputs/summary_by_currency_and_method.csv",
        "outputs/exceptions.csv",
    ]
    summary_artifact = payload["required_artifacts"][1]
    assert summary_artifact["artifact_type"] == "summary_csv"
    assert summary_artifact["summary_group_keys"] == ["currency", "payment_method"]
    assert [metric["name"] for metric in summary_artifact["summary_metrics"]] == [
        "payable_amount"
    ]
    assert any(
        "every path listed in REQUIRED_ARTIFACTS exists after the agent run and can be opened and read successfully"
        in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "required exception_csv artifact has zero data rows" in item
        for item in payload["test_quality_requirements"]
    )
    assert any(
        "required summary_csv artifact is listed" in item
        for item in payload["test_quality_requirements"]
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1000", "1000"),
        ("1,000.50", "1000.50"),
        ("", None),
        (None, None),
        (Decimal("42.5"), "42.5"),
    ],
)
def test_safe_decimal_handles_common_finance_cell_values(value: object, expected: str | None) -> None:
    from agentforge.orchestrator.generated_agent_coercion import safe_decimal

    result = safe_decimal(value)
    if expected is None:
        assert result is None
    else:
        assert result == Decimal(expected)


def test_parse_date_handles_common_date_strings_and_blanks() -> None:
    from datetime import date

    from agentforge.orchestrator.generated_agent_coercion import parse_date

    assert parse_date("2026-03-14") == date(2026, 3, 14)
    assert parse_date("2026-03-14 00:00:00") == date(2026, 3, 14)
    assert parse_date("") is None
    assert parse_date(None) is None


def test_safe_decimal_coercion_prevents_string_subtraction_typeerror() -> None:
    from agentforge.orchestrator.generated_agent_coercion import safe_decimal

    row = {
        "invoice_amount": "2213.72",
        "paid_amount": "2384.51",
        "credit_note_amount": "0",
        "deduction_amount": "0",
    }
    result = (
        safe_decimal(row["invoice_amount"], default=Decimal("0"))
        - safe_decimal(row["paid_amount"], default=Decimal("0"))
        - safe_decimal(row["credit_note_amount"], default=Decimal("0"))
        - safe_decimal(row["deduction_amount"], default=Decimal("0"))
    )
    assert isinstance(result, Decimal)


def test_test_generation_prompt_requires_workspace_root_paths() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report",
                    "required_columns": [],
                }
            ],
            "input_columns": ["expense_id", "amount"],
            "output_columns": ["expense_id", "amount", "exception_flag"],
            "required_output_columns": ["expense_id", "exception_flag"],
        }
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="expense_exception_review",
            user_description="Review expenses and flag exceptions.",
            reviewed_contract=contract,
        )
    )
    requirements = payload["test_quality_requirements"]
    assert payload["workspace_execution_context"]["pytest_cwd"] == "session workspace root"
    assert "WORKSPACE_ROOT = Path(__file__).resolve().parents[2]" in payload[
        "workspace_execution_context"
    ]["workspace_root_resolution"]
    assert any("cwd pinned to the session workspace root" in item for item in requirements)
    assert any("WORKSPACE_ROOT = Path(__file__).resolve().parents[2]" in item for item in requirements)
    assert any("tmp_path" in item and "generated/agent.py" in item for item in requirements)
    assert "do not invoke generated/agent.py from pytest tmp_path" in payload["instruction"]


def test_fafd3422_fixture_detects_pytest_workspace_pathing_failure() -> None:
    fixture = json.loads(_FIXTURE_FAFD3422_PYTEST_PATHING.read_text(encoding="utf-8"))
    failure_detail = (
        "Generated pytest did not pass any collected tests\n"
        "pytest_command=python -m pytest -v generated/tests\n"
        "AssertionError: Agent failed: /Users/example/anaconda3/bin/python: can't open file "
        "'/private/tmp/pytest-of-user/pytest-4070/test_row_count_preserved0/generated/agent.py': "
        "[Errno 2] No such file or directory\n"
    )
    assert _is_pytest_workspace_pathing_failure(failure_detail)
    context = _pytest_workspace_pathing_failure_context(
        failure_detail=failure_detail,
        current_files=[
            {
                "path": "generated/tests/test_agent.py",
                "content": fixture["generated_test_source"],
            }
        ],
    )
    assert context is not None
    assert context["uses_tmp_path_fixture"] is True
    assert context["invokes_agent_under_tmp_path"] is True
    repair_requirements = _pytest_workspace_pathing_repair_requirements(context)
    assert any("WORKSPACE_ROOT = Path(__file__).resolve().parents[2]" in item for item in repair_requirements)
    assert any("tmp_path / 'generated/agent.py'" in item for item in repair_requirements)


def test_fafd3422_fixture_reproduces_pytest_pathing_failure(workspaces_root: Path) -> None:
    fixture = json.loads(_FIXTURE_FAFD3422_PYTEST_PATHING.read_text(encoding="utf-8"))
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    agent_path = workspace / "generated" / "agent.py"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(
        "import sys\n"
        "from pathlib import Path\n\n"
        "def main():\n"
        "    args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
        "    Path(args['--row-output']).write_text('expense_id,amount\\nE001,10\\n', encoding='utf-8')\n"
        "    Path(args['--report-path']).write_text('# report\\n', encoding='utf-8')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    (workspace / "generated" / "author_output_contract.json").write_text("{}", encoding="utf-8")
    (workspace / "uploads").mkdir(parents=True, exist_ok=True)
    (workspace / "uploads" / "input.csv").write_text("expense_id,amount\nE001,10\n", encoding="utf-8")
    test_path = workspace / "generated" / "tests" / "test_agent.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(fixture["generated_test_source"], encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "generated/tests"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "generated/agent.py" in combined
    assert "No such file" in combined or "no such file" in combined.lower()


def test_fafd3422_workspace_relative_tests_can_collect_from_workspace_root(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(_FIXTURE_FAFD3422_PYTEST_PATHING.read_text(encoding="utf-8"))
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    agent_path = workspace / "generated" / "agent.py"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(
        "import sys\n"
        "from pathlib import Path\n\n"
        "def main():\n"
        "    args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
        "    Path(args['--row-output']).write_text('expense_id,amount\\nE001,10\\n', encoding='utf-8')\n"
        "    Path(args['--report-path']).write_text('# report\\n', encoding='utf-8')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    (workspace / "generated" / "author_output_contract.json").write_text("{}", encoding="utf-8")
    (workspace / "uploads").mkdir(parents=True, exist_ok=True)
    (workspace / "uploads" / "input.csv").write_text("expense_id,amount\nE001,10\n", encoding="utf-8")
    (workspace / "outputs").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs" / "output.csv").write_text("expense_id,amount\nE001,10\n", encoding="utf-8")
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / "reports" / "validation_report.md").write_text("# report\n", encoding="utf-8")
    test_path = workspace / "generated" / "tests" / "test_agent.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(fixture["workspace_relative_test_source"], encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "generated/tests"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixture_3bb42792_reproduces_reserved_path_safety_failure() -> None:
    fixture = json.loads(_FIXTURE_3BB42792_EVENT_LOG_CORRUPTION.read_text(encoding="utf-8"))
    issues = _safety_issues_for_files(
        {"generated/agent.py": fixture["generated_agent_source"]}
    )
    assert issues["generated/agent.py"] == sorted(
        fixture["unsafe_findings"]["generated/agent.py"]
    )


def test_scan_detects_events_jsonl_direct_open_write() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = "with open('events.jsonl', 'w') as handle:\n    handle.write('{}\\n')\n"
    assert "write to reserved path: events.jsonl" in _scan_model_code(source)


def test_scan_detects_events_jsonl_via_variable_indirection() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = (
        "events_path = 'events.jsonl'\n"
        "with open(events_path, 'w') as handle:\n"
        "    handle.write('{}\\n')\n"
    )
    assert "write to reserved path: events.jsonl" in _scan_model_code(source)


def test_scan_detects_manifest_json_write() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = "with open('manifest.json', 'w') as handle:\n    handle.write('{}')\n"
    assert "write to reserved path: manifest.json" in _scan_model_code(source)


def test_scan_detects_session_readme_write() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = "from pathlib import Path\nPath('SESSION_README.md').write_text('# readme\\n', encoding='utf-8')\n"
    assert "write to reserved path: SESSION_README.md" in _scan_model_code(source)


def test_scan_detects_archive_zip_write() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = "import zipfile\nzipfile.ZipFile('archive.zip', 'w').write('outputs/output.csv')\n"
    assert "write to reserved path: archive.zip" in _scan_model_code(source)


def test_scan_detects_model_authoring_summary_write() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = (
        "summary_path = 'reports/model_authoring_summary.md'\n"
        "open(summary_path, 'w').write('# summary\\n')\n"
    )
    assert "write to reserved path: reports/model_authoring_summary.md" in _scan_model_code(source)


def test_scan_allows_contract_declared_output_writes() -> None:
    from agentforge.orchestrator.author_llm_authoring import _scan_model_code

    source = (
        "with open('outputs/output.csv', 'w') as handle:\n"
        "    handle.write('expense_id,amount\\n')\n"
        "with open('reports/validation_report.md', 'w') as handle:\n"
        "    handle.write('# report\\n')\n"
    )
    reserved = [issue for issue in _scan_model_code(source) if issue.startswith("write to reserved path:")]
    assert reserved == []


def test_codegen_prompt_forbids_backend_orchestration_writes() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
        }
    )
    prompt = _codegen_prompt(
        workflow_type="expense_exception_review",
        user_description="Review expenses and flag exceptions.",
        reviewed_contract=contract,
    )
    assert "Never write backend orchestration or audit control files" in prompt
    assert "events.jsonl" in prompt
    assert "manifest.json" in prompt
    assert "SESSION_README.md" in prompt


def test_safety_repair_prompt_forbids_reserved_path_writes() -> None:
    fixture = json.loads(_FIXTURE_3BB42792_EVENT_LOG_CORRUPTION.read_text(encoding="utf-8"))
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
        }
    )
    prompt = json.loads(
        _safety_repair_prompt(
            reviewed_contract=contract,
            files={"generated/agent.py": fixture["generated_agent_source"]},
            unsafe_findings=fixture["unsafe_findings"],
        )
    )
    assert "Never write backend orchestration or audit control files" in prompt["instruction"]
    assert "events.jsonl" in prompt["instruction"]
    assert "write to reserved path: events.jsonl" in prompt["unsafe_findings"]["generated/agent.py"]


def test_requested_deliverables_guidance_excludes_backend_managed_paths() -> None:
    from agentforge.orchestrator.author_llm_authoring import _requested_deliverables_guidance

    guidance = _requested_deliverables_guidance()
    joined = " ".join(guidance["planning_rules"])
    assert "events.jsonl" in joined
    assert "manifest.json" in joined
    assert "SESSION_README.md" in joined
    assert "archive.zip" in joined


def test_required_deliverable_discipline_rejects_backend_managed_events_jsonl() -> None:
    from agentforge.orchestrator.author_llm_authoring import (
        ContractSemanticValidationError,
        _validate_contract_payload_strict,
    )

    payload = {
        "workflow_type": "expense_exception_review",
        "build_mode": "llm_custom",
        "input_file": "uploads/input.csv",
        "input_format": "csv",
        "row_level_output_file": "outputs/output.csv",
        "input_columns": ["expense_id"],
        "output_columns": ["expense_id", "exception_flag"],
        "required_output_columns": ["exception_flag"],
        "output_column_semantics": [
            {
                "name": "exception_flag",
                "description": "Exception flag",
                "producer_kind": "exception_flag",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Flag rows that need review.",
                "fallback_value_semantics": "Use no_exception when no rule matches.",
            }
        ],
        "requested_deliverables": [
            {
                "name": "Event log",
                "description": "JSONL event logs from the agent run",
                "output_path": "events.jsonl",
                "required": True,
                "source": "user_explicit",
            }
        ],
    }
    with pytest.raises(ContractSemanticValidationError, match="backend-managed"):
        _validate_contract_payload_strict(payload)


def test_last_event_id_raises_event_log_error_on_malformed_line(tmp_path: Path) -> None:
    from uuid import uuid4

    from agentforge.persistence.event_log import EventLog, EventLogError
    from agentforge.persistence.workspace import WorkspaceManager
    from agentforge.schemas import ActorType, EventKind, Workflow

    wm = WorkspaceManager(root=tmp_path)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    log = EventLog(wm)
    log.append(
        session_id=sid,
        kind=EventKind.WORKSPACE_ALLOCATED,
        actor_type=ActorType.SYSTEM,
        payload={"note": "seed"},
    )
    events_path = wm.get(sid) / "events.jsonl"
    events_path.write_text(
        '{"event": "processing_started", "timestamp": "2026-05-25T12:52:34Z"}\n',
        encoding="utf-8",
    )
    with pytest.raises(EventLogError, match="missing required event id"):
        log.append(
            session_id=sid,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={"note": "after corruption"},
        )


def _demo_shaped_bank_contract() -> AuthorOutputContract:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    return AuthorOutputContract.model_validate(
        {
            **contract.model_dump(mode="json"),
            "workflow_type": "model_authored_finance_workflow",
            "build_mode": "llm_custom",
            "validation_checks": [
                {
                    "check_id": "clear_rule_confidence_08",
                    "layer": "business_rules",
                    "name": "Clear keyword rule confidence at least 0.80",
                    "required": True,
                    "check_type": "formula",
                    "formula": (
                        "(rule_matched == '' or rule_matched is null or "
                        "confidence_score >= 0.80)"
                    ),
                    "input_columns": ["rule_matched", "confidence_score"],
                    "output_column": "confidence_score",
                    "severity": "error",
                }
            ],
        }
    )


def test_reference_sample_codegen_omits_hidden_demo_guidance_without_scaffold() -> None:
    contract = _demo_shaped_bank_contract()
    prompt = _codegen_prompt(
        workflow_type="model_authored_finance_workflow",
        user_description=_BANK_REFERENCE_USER_DESCRIPTION,
        reviewed_contract=contract,
        template_hint="bank_categoriser",
        schema_profile=_bank_reference_schema_profile(),
        enable_bundled_bank_reference_guidance=False,
    )
    assert "Demo-shaped bank transaction categorisation constraints" not in prompt
    assert "Bundled bank reference sample constraints" not in prompt
    assert "BTX-0001" not in prompt
    assert _BANK_REFERENCE_USER_DESCRIPTION in prompt


def test_classification_test_generation_requirements_cover_clear_rule_formula() -> None:
    contract = _demo_shaped_bank_contract()
    requirements = _classification_test_generation_requirements(contract)
    assert any("clear_rule_confidence_08" in item for item in requirements)
    assert any("No rule matched" in item for item in requirements)
    assert any("allowed_enums.category label" in item for item in requirements)


def test_reference_sample_test_prompt_omits_hidden_demo_requirements_without_scaffold() -> None:
    contract = _demo_shaped_bank_contract()
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="model_authored_finance_workflow",
            user_description=_BANK_REFERENCE_USER_DESCRIPTION,
            reviewed_contract=contract,
            template_hint="bank_categoriser",
            enable_bundled_bank_reference_guidance=False,
        )
    )
    requirements = payload["test_quality_requirements"]
    assert any("clear_rule_confidence_08" in item for item in requirements)
    assert not any("demo-shaped bank categorisation upload" in item for item in requirements)
    assert not any("For transaction_id BTX-0001" in item for item in requirements)


def test_bank_generated_pytest_failure_fixture_documents_mixed_root_cause() -> None:
    fixture = json.loads(_FIXTURE_BANK_GENERATED_PYTEST_FAILURE.read_text(encoding="utf-8"))
    assert fixture["root_cause_classification"] == 7
    assert "test_confidence_score_constraints" in fixture["failing_tests"][1]
    assert "No rule matched" in fixture["assertion_failures"][1]
    assert "substring keyword" in fixture["expected_fix"]


def test_expense_pytest_after_golden_fixture_documents_brittle_test_root_cause() -> None:
    fixture = json.loads(_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN.read_text(encoding="utf-8"))
    assert fixture["session_id"] == "9980bf32-d3f1-4b88-87ce-15768ac92e91"
    assert fixture["root_cause_classification"] == "F"
    assert fixture["golden_status"] == "PASS"
    assert "test_allowed_enums" in fixture["failing_tests"][0]
    assert "suspicious_keywords" in fixture["assertion_failures"][0]
    assert "stable field" in fixture["expected_fix"].lower()


def test_expense_testgen_prefers_stable_fields_over_exact_prose() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag", "rule_used"],
                    "optional_columns": [],
                }
            ],
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                }
            ],
            "allowed_enums": {
                "exception_flag": ["no_issue", "review_required"],
                "review_required": ["yes", "no"],
                "severity": ["none", "low", "medium", "high"],
            },
            "input_columns": ["expense_id", "amount", "policy_limit"],
            "output_columns": [
                "expense_id",
                "exception_flag",
                "exception_reason",
                "review_required",
                "severity",
                "rule_used",
            ],
            "required_output_columns": [
                "exception_flag",
                "exception_reason",
                "review_required",
                "severity",
                "rule_used",
            ],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="expense_exception_review",
            user_description=(
                "Review this expense report, flag policy exceptions, and return all rows "
                "plus a separate exceptions file."
            ),
            reviewed_contract=contract,
            schema_profile={
                "columns": [
                    "expense_id",
                    "amount",
                    "policy_limit",
                    "receipt_attached",
                    "approval_status",
                    "notes",
                ],
                "row_count": 7,
            },
        )
    )
    joined = "\n".join(payload["test_quality_requirements"])
    assert "stable structured field assertions" in joined.lower()
    assert "do not require exact exception_reason prose" in joined.lower()
    assert "do not invent a separate hardcoded rule_used enum set" in joined.lower()
    assert "outputs/output.csv" in payload["required_artifacts"][0]["path"]
    assert payload["workspace_execution_context"]["outputs_dir"] == "outputs/"


def test_expense_pytest_brittle_failure_detection_and_repair_requirements() -> None:
    fixture = json.loads(_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN.read_text(encoding="utf-8"))
    failure_detail = (
        "generated/tests/test_agent.py::test_allowed_enums FAILED\n"
        f"{fixture['assertion_failures'][0]}\n"
        "generated/tests/test_agent.py::test_exception_rules_logic FAILED\n"
        f"{fixture['assertion_failures'][1]}\n"
    )
    assert _is_expense_exception_pytest_brittle_failure(failure_detail)
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag", "rule_used"],
            "required_output_columns": ["exception_flag", "rule_used"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    repair_requirements = _expense_exception_pytest_repair_requirements(contract=contract)
    assert any("stable field checks" in item.lower() for item in repair_requirements)
    assert any("amount_over_limit" in item for item in repair_requirements)
    assert any("exact exception_reason" in item for item in repair_requirements)


def test_eed97570_fixture_documents_multi_rule_enum_and_exception_corruption() -> None:
    fixture = json.loads(_FIXTURE_EED97570_MULTI_RULE.read_text(encoding="utf-8"))
    assert fixture["session_id"] == "eed97570-e992-4b67-bce6-01dbe8051e2a"
    assert fixture["root_cause_classification"] == "F"
    assert fixture["golden_status"] == "PASS"
    assert ";" in fixture["multi_rule_row"]["rule_used"]
    assert fixture["exceptions_csv_after_pytest"]["row_count"] == 1
    assert "semicolon-separated" in fixture["expected_fix"].lower()


def test_expense_testgen_allows_semicolon_rule_used_containment() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                },
                {
                    "name": "missing_receipt",
                    "condition": "receipt_attached != 'true'",
                    "reason": "Missing receipt",
                    "severity": "high",
                    "output_column": "exception_flag",
                },
            ],
            "allowed_enums": {
                "exception_flag": ["no_issue", "exception"],
                "review_required": ["yes", "no"],
                "rule_used": ["amount_over_limit", "missing_receipt", "none"],
            },
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag", "review_required", "rule_used"],
            "required_output_columns": ["exception_flag", "review_required"],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="expense_exception_review",
            user_description=(
                "Review this expense report, flag policy exceptions, and return all rows "
                "plus a separate exceptions file."
            ),
            reviewed_contract=contract,
            schema_profile={
                "columns": [
                    "expense_id",
                    "amount",
                    "policy_limit",
                    "receipt_attached",
                    "approval_status",
                    "notes",
                ],
                "row_count": 7,
            },
        )
    )
    joined = "\n".join(payload["test_quality_requirements"])
    assert "semicolon-separated token" in joined.lower()
    assert "outputs/exceptions.csv" in joined


def test_expense_contract_normalization_adds_review_required_to_codegen_prompt() -> None:
    from agentforge.orchestrator.author_llm_authoring import (
        apply_expense_exception_golden_policy,
    )

    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "model_authored_finance_workflow",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                }
            ],
            "input_columns": ["expense_id", "amount", "policy_limit"],
            "output_columns": ["expense_id", "exception_flag", "exception_reason"],
            "required_output_columns": ["expense_id", "exception_flag", "exception_reason"],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    normalized = apply_expense_exception_golden_policy(contract)
    prompt = _codegen_prompt(
        workflow_type="expense_exception_review",
        user_description=(
            "Review this expense report, flag policy exceptions, and return all rows "
            "plus a separate exceptions file."
        ),
        reviewed_contract=normalized,
    )
    assert "review_required" in prompt
    assert "Exception-review rule implementation" in prompt
    assert "- review_required: required=True" in prompt


def test_expense_pytest_brittle_detects_multi_rule_enum_failure() -> None:
    fixture = json.loads(_FIXTURE_EED97570_MULTI_RULE.read_text(encoding="utf-8"))
    failure_detail = (
        "generated/tests/test_agent.py::test_allowed_enums FAILED\n"
        f"{fixture['assertion_failures'][0]}\n"
    )
    assert _is_expense_exception_pytest_brittle_failure(failure_detail)


def test_expense_pytest_brittle_detects_semicolon_rule_used_and_synth_exception_path() -> None:
    """Session 4e6edafe: comma-split rule_used + missing synth_exceptions.csv."""
    failure_detail = (
        "generated/tests/test_agent.py::test_rule_used_permitted_names FAILED\n"
        "AssertionError: Rule name 'amount_over_limit; missing_receipt; approval_not_final; "
        "suspicious_notes' not in permitted names: {'amount_over_limit', 'missing_receipt'}\n"
        "generated/tests/test_agent.py::test_synthetic_amount_over_limit_exception FAILED\n"
        "FileNotFoundError: outputs/synth_exceptions.csv\n"
    )
    assert _is_expense_exception_pytest_brittle_failure(failure_detail)


def test_expense_testgen_warns_contract_exception_path_not_synth_sibling() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag", "rule_used"],
            "required_output_columns": ["exception_flag", "rule_used"],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="expense_exception_review",
            user_description="Review expenses and return exceptions file.",
            reviewed_contract=contract,
            schema_profile={
                "columns": [
                    "expense_id",
                    "amount",
                    "policy_limit",
                    "receipt_attached",
                    "approval_status",
                    "notes",
                ],
                "row_count": 7,
            },
        )
    )
    joined = "\n".join(payload["test_quality_requirements"]).lower()
    assert "exception_output_files" in joined
    assert "synth_exceptions" in joined
    assert "split on ';'" in joined or "semicolon-separated" in joined


def test_expense_pytest_repair_mentions_multi_rule_containment() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag", "rule_used"],
            "required_output_columns": ["exception_flag", "rule_used"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    repair_requirements = _expense_exception_pytest_repair_requirements(contract=contract)
    joined = "\n".join(repair_requirements).lower()
    assert "each token is permitted" in joined


def test_test_generation_prompt_warns_exception_output_required_columns_are_strings() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag"],
            "required_output_columns": ["exception_flag"],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    payload = json.loads(
        _test_generation_prompt(
            workflow_type="expense_exception_review",
            user_description="Review expenses.",
            reviewed_contract=contract,
            schema_profile={
                "columns": [
                    "expense_id",
                    "amount",
                    "policy_limit",
                    "receipt_attached",
                    "approval_status",
                    "notes",
                ],
                "row_count": 7,
            },
        )
    )
    shape = "\n".join(payload["contract_runtime_shape_requirements"])
    assert "exception_output_files[].required_columns" in shape
    assert "never use col['name']" in shape


def test_repair_candidate_test_workspace_root_uses_path_depth() -> None:
    assert _workspace_root_parent_index("generated/tests/test_agent.py") == 2
    assert (
        _workspace_root_parent_index("generated/repairs/attempt_1/tests/test_agent.py")
        == 4
    )
    source = (
        "from pathlib import Path\n"
        "WORKSPACE_ROOT = Path(__file__).resolve().parents[2]\n"
    )
    normalized = _normalize_generated_test_workspace_root(
        source,
        "generated/repairs/attempt_1/tests/test_agent.py",
    )
    assert "parents[4]" in normalized
    assert "parents[2]" not in normalized


def test_stage_repair_candidate_files_normalizes_workspace_root(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    source = (
        "from pathlib import Path\n"
        "WORKSPACE_ROOT = Path(__file__).resolve().parents[2]\n"
    )
    candidate_paths = _stage_repair_candidate_files(
        workspace=workspace,
        files={"generated/tests/test_agent.py": source},
        attempt=1,
    )
    assert candidate_paths == ["generated/repairs/attempt_1/tests/test_agent.py"]
    staged = (workspace / candidate_paths[0]).read_text(encoding="utf-8")
    assert "parents[4]" in staged


def test_classification_test_generation_discourages_snake_case_report_literals() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["transaction_id", "amount"],
            "output_columns": ["transaction_id", "amount", "category"],
            "required_output_columns": ["category"],
            "summary_metrics": [
                "transaction_count",
                "total_debits",
                "total_credits",
                "net_amount",
            ],
        }
    )
    requirements = _classification_test_generation_requirements(contract)
    joined = "\n".join(requirements)
    assert "internal identifiers" in joined
    assert "do not assert raw snake_case" in joined
    assert "Transaction Count" in joined
