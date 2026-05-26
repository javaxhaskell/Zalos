"""Reference-sample honesty tests against the maximal take-home brief."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from agentforge.config import Settings
from agentforge.models import FakeModelClient
from agentforge.orchestrator.author_llm_authoring import (
    _bundled_bank_reference_contract_scaffold,
    _codegen_prompt,
    collect_author_model_stages,
    effective_author_model_stages,
    model_contributed_files_from_events,
    run_model_authoring_pipeline,
    stage_bundled_bank_reference_golden,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, EventKind, Workflow
from tests.author_model_fixtures import model_authoring_responses
from tests.test_author_codegen_reliability import (
    _BANK_REFERENCE_USER_DESCRIPTION,
    _bank_reference_schema_profile,
)
from tests.test_author_completion_gate import _base_script, _run_pipeline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXPENSE_DEMO_CSV = (
    _REPO_ROOT
    / "apps/web/app/api/reference-samples/expense-exception-review/expense_exception_review.csv"
)
_BLIND_EVAL_EXPENSE_CSV = (
    _REPO_ROOT / "blind_eval_cases/expense_exception_review/input.csv"
)
_EXPENSE_GOLDEN_CSV = (
    _REPO_ROOT / "evals/golden/expense_exception_review/expected_output.csv"
)
_EXPENSE_DEMO_FILENAME = "expense_exception_review.csv"
_EXPENSE_REFERENCE_USER_DESCRIPTION = (
    "Build context: Expense Exception Review\n\n"
    "Review this expense report, flag policy exceptions, and return all rows plus "
    "a separate exceptions file."
)


def _expense_reference_schema_profile() -> dict[str, object]:
    return {
        "columns": [
            "expense_id",
            "employee_id",
            "expense_date",
            "category",
            "amount",
            "currency",
            "merchant",
            "approval_status",
            "receipt_attached",
            "policy_limit",
            "notes",
        ],
        "row_count": 7,
        "upload_format": "csv",
        "agent_input_path": f"uploads/{_EXPENSE_DEMO_FILENAME}",
        "normalized_input_path": f"uploads/{_EXPENSE_DEMO_FILENAME}",
        "sample_rows": [],
    }


def _csv_expense_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row["expense_id"] for row in csv.DictReader(handle)]


def test_expense_reference_sample_user_message_shows_expense_exception_review() -> None:
    assert _EXPENSE_REFERENCE_USER_DESCRIPTION.startswith(
        "Build context: Expense Exception Review"
    )


def test_expense_reference_sample_matches_blind_eval_canonical_input() -> None:
    assert _EXPENSE_DEMO_CSV.read_bytes() == _BLIND_EVAL_EXPENSE_CSV.read_bytes()


def test_expense_golden_pairs_with_canonical_reference_sample() -> None:
    sample_ids = _csv_expense_ids(_EXPENSE_DEMO_CSV)
    golden_ids = _csv_expense_ids(_EXPENSE_GOLDEN_CSV)
    assert len(sample_ids) == 7
    assert sample_ids == golden_ids
    assert len(set(sample_ids)) == 7


def _record_template_hint(event_log: EventLog, sid, template_hint: str | None) -> None:
    if not template_hint:
        return
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={"kind": "template_hint", "value": template_hint},
        step=0,
    )


def test_reference_author_sample_runs_same_model_stages_as_custom_upload(
    workspaces_root,
) -> None:
    result, event_log, sid, _workspace = _run_pipeline(workspaces_root, _base_script())
    assert result.ok is True
    stages = collect_author_model_stages(event_log.read_all(sid))
    assert stages == [
        "contract_planning",
        "contract_review",
        "code_generation",
        "test_generation",
    ]
    scaffold_events = [
        event
        for event in event_log.read_all(sid)
        if event.kind == EventKind.DECISION_INPUT
        and (event.payload or {}).get("kind") == "reference_sample_contract_scaffold_used"
    ]
    assert scaffold_events == []


def test_reference_author_sample_does_not_bypass_model_contract_or_tests(
    workspaces_root,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    demo_dest = workspace / "uploads" / _EXPENSE_DEMO_FILENAME
    demo_dest.parent.mkdir(parents=True, exist_ok=True)
    demo_dest.write_bytes(_EXPENSE_DEMO_CSV.read_bytes())
    event_log = EventLog(wm)
    _record_template_hint(event_log, sid, "expense_exception_review")
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_reference_honesty",
            model_client=FakeModelClient(script=[]),
            workspace=workspace,
            user_description=_EXPENSE_REFERENCE_USER_DESCRIPTION,
            schema_profile=_expense_reference_schema_profile(),
            reference_scaffold_root=None,
            workflow_type="model_authored_finance_workflow",
            template_hint="expense_exception_review",
            settings=Settings(author_enable_bank_reference_scaffold=False),
        )
    )
    assert result.ok is False
    scaffold_events = [
        event
        for event in event_log.read_all(sid)
        if event.kind == EventKind.DECISION_INPUT
        and (event.payload or {}).get("kind") == "reference_sample_contract_scaffold_used"
    ]
    assert scaffold_events == []
    assert not (workspace / "generated" / "agent.py").is_file()


def test_custom_author_upload_path_still_works(workspaces_root) -> None:
    script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="payment_processor_reconciliation",
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
            stage_label="test_reference_honesty",
            model_client=FakeModelClient(script=script),
            workspace=workspace,
            user_description="Reconcile uploaded payment rows and flag exceptions.",
            schema_profile={
                "columns": [
                    "transaction_id",
                    "processor",
                    "settlement_batch",
                    "gross_amount",
                    "fee_amount",
                    "net_amount",
                    "status",
                ],
                "row_count": 10,
                "upload_format": "xlsx",
                "selected_sheet": "Sheet1",
                "normalized_input_path": "uploads/normalised_input.csv",
                "agent_input_path": "uploads/normalised_input.csv",
            },
            reference_scaffold_root=None,
            workflow_type="payment_processor_reconciliation",
            template_hint=None,
            settings=Settings(author_enable_bank_reference_scaffold=False),
        )
    )
    assert result.ok is True
    assert model_contributed_files_from_events(event_log.read_all(sid))


def test_repair_builtin_fixture_loads_via_api(app_client: TestClient) -> None:
    session = app_client.post("/sessions", json={"workflow": "repair"}).json()
    sid = session["id"]
    resp = app_client.post(f"/sessions/{sid}/load_fixture/invoice_aging_v2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fixture_name"] == "invoice_aging_v2"
    assert body["staged_golden_path"] is not None


def test_custom_repair_upload_endpoint_exists(app_client: TestClient) -> None:
    session = app_client.post("/sessions", json={"workflow": "repair"}).json()
    sid = session["id"]
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("not-a-zip.txt", b"plain text", "text/plain")},
    )
    assert resp.status_code in {400, 415, 422}


def test_golden_output_is_not_codegen_shortcut() -> None:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    prompt = _codegen_prompt(
        workflow_type="model_authored_finance_workflow",
        user_description=_BANK_REFERENCE_USER_DESCRIPTION,
        reviewed_contract=contract,
        template_hint="bank_categoriser",
        schema_profile=_bank_reference_schema_profile(),
        enable_bundled_bank_reference_guidance=False,
    )
    golden_section = prompt.split("Golden-output separation:", 1)
    assert len(golden_section) == 2
    assert "validation oracles only" in golden_section[1]
    assert "must NOT read golden or expected-output files" in golden_section[1]
    pre_golden = golden_section[0]
    assert "evals/golden_output.csv" not in pre_golden
    assert "expected_output.csv" not in pre_golden
    assert "golden_output_path" not in pre_golden


def test_no_backend_function_writes_bank_demo_outputs(tmp_path: Path) -> None:
    workspace = tmp_path / "session"
    workspace.mkdir()
    assert stage_bundled_bank_reference_golden(workspace=workspace) is True
    golden = workspace / "evals" / "golden_output.csv"
    assert golden.is_file()
    output_csv = workspace / "outputs" / "output.csv"
    assert not output_csv.exists()


def test_reports_distinguish_model_provenance_from_scaffold_events(
    workspaces_root,
) -> None:
    result, event_log, sid, _workspace = _run_pipeline(workspaces_root, _base_script())
    assert result.ok is True
    provenance = next(
        event.payload
        for event in event_log.read_all(sid)
        if event.kind == EventKind.DECISION_INPUT
        and (event.payload or {}).get("kind") == "model_authoring_provenance"
    )
    assert provenance["model_contributed_files"]
    scaffold_events = [
        event.payload
        for event in event_log.read_all(sid)
        if event.kind == EventKind.DECISION_INPUT
        and (event.payload or {}).get("kind") == "reference_sample_contract_scaffold_used"
    ]
    assert scaffold_events == []
    assert effective_author_model_stages(event_log.read_all(sid)) == collect_author_model_stages(
        event_log.read_all(sid)
    )
