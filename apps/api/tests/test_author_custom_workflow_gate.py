"""Author custom workflow build routing and evidence-gated completion."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
import shutil
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import Settings, get_settings
from agentforge.models import FakeModelClient, ModelResponse, TextBlock
from agentforge.models.ollama_client import OllamaModelClient
from agentforge.orchestrator import AuthorFlow
from agentforge.orchestrator.author_custom_build import (
    UploadedDataFile,
    _author_generated_code_failure_message,
    _bank_reference_sample_validation_checks,
    _contract_validation_failure_detail,
    _pytest_assertion_lines,
    _pytest_failures,
    _pytest_failure_requires_agent_repair,
    _pytest_failure_repair_kind,
    _pytest_signals_expense_brittle_test_failure,
    _pytest_signals_classification_agent_failure,
    _pytest_signals_clear_rule_confidence_test_misinterpretation,
    execute_custom_workflow_pipeline,
)
from agentforge.orchestrator.author_contract_validation import validate_against_contract
from agentforge.orchestrator.author_intent import assess_author_pre_pipeline
from agentforge.orchestrator.author_llm_authoring import (
    AI_AUTHORED_WORKFLOW_BUILD_VIA,
    _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES,
    _BUNDLED_BANK_REFERENCE_EXPECTED_CATEGORY_COUNTS,
    _bundled_bank_reference_contract_scaffold,
    apply_bundled_bank_reference_golden_policy,
    repair_generated_author_files,
    stage_bundled_bank_reference_golden,
)
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ErrorCode,
    EventKind,
    SessionStatus,
    ValidationCheck,
    ValidationLayer,
    ValidationReport,
    Workflow,
)
from agentforge.schemas.author_output_contract import AuthorOutputContract
from agentforge.tools import build_registry
from tests.author_model_fixtures import model_ack_only_responses, model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PAYMENT_DIR = (
    _REPO_ROOT / "templates" / "custom_workflows" / "payment_processor_reconciliation"
)
_FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/vendor_payment_missing_required_artifacts.json"
)
_FIXTURE_BANK_DEMO_TUPLE_ARITY_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_demo_tuple_arity_failure_46efe089.json"
)
_FIXTURE_BANK_DEMO_EXCEPTION_CSV_FIELDNAMES_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_demo_exception_csv_fieldnames_failure_f2f84399.json"
)
_FIXTURE_BANK_DEMO_EXPECTED_OUTPUT = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_demo_expected_output.csv"
)
_FIXTURE_FAFD3422_PYTEST_PATHING = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/expense_generated_pytest_pathing_failure_fafd3422.json"
)
_FIXTURE_BANK_GENERATED_PYTEST_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/bank_generated_pytest_failure_5aa804ba.json"
)
_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/expense_generated_pytest_failure_after_golden_pass_9980bf32.json"
)


def _payment_model_responses() -> list[ModelResponse]:
    return model_authoring_responses(
        template_root=_PAYMENT_DIR,
        workflow_type="payment_processor_reconciliation",
        contract_overrides={
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["outputs/summary_by_settlement_batch.csv"],
        },
    )


def _response(id_: str, payload: dict) -> ModelResponse:
    return ModelResponse(
        id=id_,
        content=[TextBlock(text=json.dumps(payload, indent=2))],
        stop_reason="end_turn",
    )


def _replace_agent_source(response: ModelResponse, source: str) -> ModelResponse:
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        return ModelResponse(
            id=f"{response.id}-replacement",
            content=[TextBlock(text=source)],
            stop_reason=response.stop_reason,
            usage=response.usage,
        )
    payload["files"] = [
        {
            **item,
            "content": source if item.get("path") == "generated/agent.py" else item["content"],
        }
        for item in payload["files"]
    ]
    return _response(f"{response.id}-replacement", payload)


def _replace_test_source(response: ModelResponse, source: str) -> ModelResponse:
    payload = json.loads(response.text)
    payload["files"] = [
        {
            **item,
            "content": (
                source
                if item.get("path") == "generated/tests/test_agent.py"
                else item["content"]
            ),
        }
        for item in payload["files"]
    ]
    return _response(f"{response.id}-replacement", payload)


def _agent_source_from(response: ModelResponse) -> str:
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        return response.text
    for item in payload["files"]:
        if item["path"] == "generated/agent.py":
            return item["content"]
    raise AssertionError("generated/agent.py missing from response")


_PAYMENT_RECON_DESCRIPTION = (
    "Build a payment processor reconciliation agent that reads processor "
    "settlements, matches gross amounts and fees to net payouts, and flags "
    "unmatched settlement rows."
)
_PAYMENT_RECON_COLUMNS = [
    "transaction_id",
    "processor",
    "settlement_batch",
    "gross_amount",
    "fee_amount",
    "net_amount",
    "status",
]


@pytest.fixture()
def e2e_db(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'e2e.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


_LIVE_EXPORT_COLUMNS = [
    "payout_id",
    "transaction_id",
    "transaction_date",
    "processor",
    "currency",
    "gross_amount",
    "fee_amount",
    "net_amount",
    "settlement_batch",
    "settlement_date",
    "status",
    "customer_reference",
    "notes",
]
_LIVE_EXPORT_PROMPT = (
    "Build an agent that reviews this payment processor export and prepares it "
    "for finance review. It should read the uploaded spreadsheet, calculate or "
    "verify the net amounts, identify rows that may need investigation, summarise "
    "settlement batches, and produce a clean output file with validation evidence."
)


def _write_processor_export_xlsx(
    path: Path,
    *,
    rows: int = 80,
    mixed_edge_cases: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "processor_export"
    sheet.append(_LIVE_EXPORT_COLUMNS)
    statuses = ["settled", "pending", "failed", "refunded", "settled", "chargeback", "settled"]
    for idx in range(rows):
        gross = Decimal("100.00") + Decimal(idx)
        fee = (gross * Decimal("0.029")).quantize(Decimal("0.01"))
        net = gross - fee
        status = "settled"
        ref = f"CUST-{idx:04d}"
        notes = ""
        if mixed_edge_cases:
            if idx % 17 == 0:
                net = net - Decimal("0.05")
            status = statuses[idx % len(statuses)]
            if idx % 11 == 0:
                ref = ""
            if idx % 23 == 0:
                notes = "chargeback review"
        sheet.append(
            [
                f"PAY-{idx:04d}",
                f"TXN-{idx:04d}",
                "2024-01-15",
                "Stripe" if idx % 2 == 0 else "Adyen",
                "USD",
                float(gross),
                float(fee),
                float(net),
                f"BATCH-{idx // 10:03d}",
                "2024-01-16",
                status,
                ref,
                notes,
            ]
        )
    workbook.save(path)
    workbook.close()


def _write_payment_recon_xlsx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(_PAYMENT_RECON_COLUMNS)
    sheet.append(["TXN-001", "Stripe", "BATCH-001", 1000.0, 29.0, 971.0, "settled"])
    sheet.append(["TXN-002", "Stripe", "BATCH-001", 500.0, 14.5, 485.5, "settled"])
    workbook.save(path)
    workbook.close()

def _record_run_context(
    event_log: EventLog,
    session_id,
    *,
    template_hint: str | None = "bank_categoriser",
    workflow_text: str,
) -> None:
    if template_hint:
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.USER,
            payload={"kind": "template_hint", "value": template_hint},
            step=0,
        )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={"kind": "author_user_workflow", "text": workflow_text},
        step=0,
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.FILE_UPLOADED,
        actor_type=ActorType.USER,
        payload={
            "filename": "payment_processor_reconciliation_sample.xlsx",
            "relative_path": "uploads/payment_processor_reconciliation_sample.xlsx",
            "size_bytes": 2048,
        },
        step=0,
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


async def _run_author_payment_recon_xlsx(
    workspaces_root: Path,
    e2e_db,
    *,
    template_hint: str | None,
):
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    settings = get_settings()
    _write_payment_recon_xlsx(
        workspace / "uploads" / "payment_processor_reconciliation_sample.xlsx"
    )
    event_log = EventLog(wm)
    _record_run_context(
        event_log,
        sid,
        template_hint=template_hint,
        workflow_text=_PAYMENT_RECON_DESCRIPTION,
    )
    loop = AgentLoop(
        registry=build_registry(),
        model_client=FakeModelClient(script=_payment_model_responses()),
        idempotency_store=IdempotencyStore(db=e2e_db),
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    flow = AuthorFlow(
        agent_loop=loop,
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    outcome = await flow.run(
        session_id=sid,
        system_prompt="author",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=120),
    )
    return wm, event_log, sid, workspace, outcome


def test_assess_routes_payment_recon_xlsx_to_custom_build_with_bank_template() -> None:
    gate, assessment = assess_author_pre_pipeline(
        user_description=_PAYMENT_RECON_DESCRIPTION,
        template_name="bank_categoriser",
        columns=_PAYMENT_RECON_COLUMNS,
        upload_format="xlsx",
        upload_filename="payment_processor_reconciliation_sample.xlsx",
    )
    assert gate == "custom_build"
    assert assessment is None


def test_assess_routes_payment_recon_xlsx_to_custom_build_without_template() -> None:
    gate, assessment = assess_author_pre_pipeline(
        user_description=_PAYMENT_RECON_DESCRIPTION,
        template_name=None,
        columns=_PAYMENT_RECON_COLUMNS,
        upload_format="xlsx",
    )
    assert gate == "custom_build"
    assert assessment is None


def test_payment_recon_xlsx_completes_via_custom_build_with_bank_hint(
    workspaces_root: Path,
    e2e_db,
) -> None:
    async def _run() -> None:
        wm, event_log, sid, workspace, outcome = await _run_author_payment_recon_xlsx(
            workspaces_root,
            e2e_db,
            template_hint="bank_categoriser",
        )
        assert outcome.terminal_status == SessionStatus.COMPLETED
        assert outcome.terminal_error_code is None
        assert (workspace / "outputs" / "output.csv").is_file()
        assert (workspace / "generated" / "agent.py").is_file()
        assert (workspace / "generated" / "tests").is_dir()
        assert (workspace / "reports" / "validation_report.md").is_file()
        assert (workspace / "manifest.json").is_file()
        assert (workspace / "SESSION_README.md").is_file()
        assert (workspace / "reports" / "model_authoring_summary.md").is_file()

        events = event_log.read_all(sid)
        model_calls = sum(1 for event in events if event.kind == EventKind.MODEL_CALLED)
        assert model_calls > 0
        assert not any(e.kind == EventKind.TEMPLATE_SEEDED for e in events)
        assert not any(
            e.kind == EventKind.WORKFLOW_FAILED
            and e.payload.get("error_code") == "unknown"
            for e in events
        )
        completed = next(e for e in events if e.kind == EventKind.WORKFLOW_COMPLETED)
        assert completed.payload["via"] == AI_AUTHORED_WORKFLOW_BUILD_VIA
        assert completed.payload["workflow_type"] == "payment_processor_reconciliation"

        manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
        completion = manifest.get("completion") or {}
        assert completion.get("build_mode") == "llm_custom"
        assert completion.get("workflow_type") == "payment_processor_reconciliation"
        assert completion.get("input_format") == "xlsx"
        assert completion.get("normalized_input_path") == "uploads/normalised_input.csv"
        skipped = completion.get("skipped_layers") or []
        assert "golden_output" in skipped

    asyncio.run(_run())


def test_payment_recon_xlsx_completes_via_custom_build_without_template_hint(
    workspaces_root: Path,
    e2e_db,
) -> None:
    async def _run() -> None:
        _, event_log, sid, workspace, outcome = await _run_author_payment_recon_xlsx(
            workspaces_root,
            e2e_db,
            template_hint=None,
        )
        assert outcome.terminal_status == SessionStatus.COMPLETED
        assert outcome.terminal_error_code is None
        assert (workspace / "outputs" / "output.csv").is_file()
        events = event_log.read_all(sid)
        assert sum(1 for event in events if event.kind == EventKind.MODEL_CALLED) > 0
        completed = next(e for e in events if e.kind == EventKind.WORKFLOW_COMPLETED)
        assert completed.payload["via"] == AI_AUTHORED_WORKFLOW_BUILD_VIA

    asyncio.run(_run())


def _direct_bank_author_script_with_agent_source(source: str) -> list[ModelResponse]:
    script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    script[2] = _replace_agent_source(script[2], source)
    return script


def _direct_bank_author_script_with_test_source(source: str) -> list[ModelResponse]:
    script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    script[3] = _replace_test_source(script[3], source)
    return script


def _bad_agent_source() -> str:
    return (
        "from __future__ import annotations\n"
        "import argparse\n"
        "\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--input', required=True)\n"
        "    parser.add_argument('--contract', required=True)\n"
        "    parser.add_argument('--row-output', required=True)\n"
        "    parser.add_argument('--report-path', required=True)\n"
        "    parser.parse_args(argv)\n"
        "    raise RuntimeError('model generated broken code')\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _validation_null_agent_source() -> str:
    return (
        "from __future__ import annotations\n"
        "import argparse\n"
        "import csv\n"
        "from pathlib import Path\n"
        "\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--input', required=True)\n"
        "    parser.add_argument('--contract', required=True)\n"
        "    parser.add_argument('--row-output', required=True)\n"
        "    parser.add_argument('--report-path', required=True)\n"
        "    args = parser.parse_args(argv)\n"
        "    Path(args.row_output).parent.mkdir(parents=True, exist_ok=True)\n"
        "    Path(args.report_path).parent.mkdir(parents=True, exist_ok=True)\n"
        "    with open(args.input, newline='', encoding='utf-8') as infile, open(args.row_output, 'w', newline='', encoding='utf-8') as outfile:\n"
        "        reader = csv.DictReader(infile)\n"
        "        fieldnames = list(reader.fieldnames or []) + ['category', 'rule_matched', 'rule_used', 'confidence']\n"
        "        writer = csv.DictWriter(outfile, fieldnames=fieldnames)\n"
        "        writer.writeheader()\n"
        "        for row in reader:\n"
        "            writer.writerow({**row, 'category': 'Income', 'rule_matched': '', 'rule_used': '', 'confidence': '1.0'})\n"
        "    Path(args.report_path).write_text('# Validation Report\\n\\nGenerated report.\\n', encoding='utf-8')\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )


def _syntax_broken_agent_source() -> str:
    return (
        "from __future__ import annotations\n"
        "import argparse\n"
        "\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--input', required=True)\n"
        "    parser.add_argument('--contract', required=True)\n"
        "    parser.add_argument('--row-output', required=True)\n"
        "    parser.add_argument('--report-path', required=True)\n"
        "    args = parser.parse_args(argv)\n"
        "    with open(args.input, mode='r', newline='') as infile,\n"
        "         open(args.row_output, mode='w', newline='') as outfile:\n"
        "        outfile.write('never executed')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _name_error_agent_source() -> str:
    return (
        "from __future__ import annotations\n"
        "import argparse\n"
        "import csv\n"
        "import json\n"
        "from pathlib import Path\n"
        "\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--input', required=True)\n"
        "    parser.add_argument('--contract', required=True)\n"
        "    parser.add_argument('--row-output', required=True)\n"
        "    parser.add_argument('--report-path', required=True)\n"
        "    args = parser.parse_args(argv)\n"
        "    with open(args.contract, encoding='utf-8') as contract_file:\n"
        "        contract = json.load(contract_file)\n"
        "    output_path = Path(args.row_output or contract['row_level_output_file'])\n"
        "    output_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    with open(args.input, mode='r', newline='') as infile, open(output_path, mode='w', newline='') as outfile:\n"
        "        reader = csv.DictReader(infile)\n"
        "        fieldnames = list(reader.fieldnames or []) + ['rule_used']\n"
        "        writer = csv.DictWriter(outfile, fieldnames=fieldnames)\n"
        "        writer.writeheader()\n"
        "        for row in reader:\n"
        "            writer.writerow({**row, 'rule_used': rule_used})\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _syntax_broken_line_number() -> int:
    target = "    with open(args.input, mode='r', newline='') as infile,"
    return _syntax_broken_agent_source().splitlines().index(target) + 1


def _contract_path_output_agent_source() -> str:
    return (
        "from __future__ import annotations\n"
        "import argparse\n"
        "import csv\n"
        "from pathlib import Path\n"
        "\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--input', required=True)\n"
        "    parser.add_argument('--contract', required=True)\n"
        "    parser.add_argument('--row-output', required=True)\n"
        "    parser.add_argument('--report-path', required=True)\n"
        "    args = parser.parse_args(argv)\n"
        "    Path(args.report_path).parent.mkdir(parents=True, exist_ok=True)\n"
        "    Path(args.report_path).write_text('# Validation Report\\n', encoding='utf-8')\n"
        "    with open(args.input, newline='') as infile, open(args.contract, 'w', newline='') as outfile:\n"
        "        reader = csv.DictReader(infile)\n"
        "        fieldnames = list(reader.fieldnames or []) + ['category']\n"
        "        writer = csv.DictWriter(outfile, fieldnames=fieldnames)\n"
        "        writer.writeheader()\n"
        "        for row in reader:\n"
        "            writer.writerow({**row, 'category': 'Uncategorised'})\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )


def _pytest_header_blind_test_source() -> str:
    return (
        "import pytest\n"
        "from subprocess import run, PIPE\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    result = run([\"python\", \"generated/agent.py\", \"--input\", \"uploads/sample_input.csv\", \"--contract\", \"generated/author_output_contract.json\", \"--row-output\", \"outputs/output.csv\", \"--report-path\", \"reports/validation_report.md\"], capture_output=True, text=True)\n"
        "    assert result.returncode == 0\n"
        "    with open(\"outputs/output.csv\", \"r\") as f:\n"
        "        output_rows = f.readlines()\n"
        "    with open(\"uploads/sample_input.csv\", \"r\") as f:\n"
        "        input_rows = f.readlines()\n"
        "    assert len(output_rows) == len(input_rows)\n"
        "\n"
        "def test_category_presence():\n"
        "    result = run([\"python\", \"generated/agent.py\", \"--input\", \"uploads/sample_input.csv\", \"--contract\", \"generated/author_output_contract.json\", \"--row-output\", \"outputs/output.csv\", \"--report-path\", \"reports/validation_report.md\"], capture_output=True, text=True)\n"
        "    assert result.returncode == 0\n"
        "    with open(\"outputs/output.csv\", \"r\") as f:\n"
        "        for line in f:\n"
        "            columns = line.strip().split(',')\n"
        "            category = columns[6]\n"
        "            assert category in ['Income', 'Office Expense', 'Travel', 'Subscriptions', 'Refund', 'Uncategorised']\n"
        "\n"
        "def test_required_columns():\n"
        "    result = run([\"python\", \"generated/agent.py\", \"--input\", \"uploads/sample_input.csv\", \"--contract\", \"generated/author_output_contract.json\", \"--row-output\", \"outputs/output.csv\", \"--report-path\", \"reports/validation_report.md\"], capture_output=True, text=True)\n"
        "    assert result.returncode == 0\n"
        "    with open(\"outputs/output.csv\", \"r\") as f:\n"
        "        header = f.readline().strip()\n"
        "        required_columns = ['txn_id', 'date', 'amount', 'description', 'counterparty', 'account', 'category', 'rule_matched', 'rule_used', 'confidence']\n"
        "        assert all(col in header for col in required_columns)\n"
        "\n"
        "def test_summary_report():\n"
        "    result = run([\"python\", \"generated/agent.py\", \"--input\", \"uploads/sample_input.csv\", \"--contract\", \"generated/author_output_contract.json\", \"--row-output\", \"outputs/output.csv\", \"--report-path\", \"reports/validation_report.md\"], capture_output=True, text=True)\n"
        "    assert result.returncode == 0\n"
        "    with open(\"reports/validation_report.md\", \"r\") as f:\n"
        "        report_content = f.read()\n"
        "    assert \"Validation report explaining rules and counts\" in report_content\n"
    )


def _pytest_contract_backed_test_source() -> str:
    return (
        "import csv\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "_CMD = [\n"
        "    sys.executable,\n"
        "    \"generated/agent.py\",\n"
        "    \"--input\",\n"
        "    \"uploads/sample_input.csv\",\n"
        "    \"--contract\",\n"
        "    \"generated/author_output_contract.json\",\n"
        "    \"--row-output\",\n"
        "    \"outputs/output.csv\",\n"
        "    \"--report-path\",\n"
        "    \"reports/validation_report.md\",\n"
        "]\n"
        "_ALLOWED = {\"Income\", \"Office Expense\", \"Travel\", \"Subscriptions\", \"Refund\", \"Uncategorised\"}\n"
        "\n"
        "def _run_agent():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "\n"
        "def _rows():\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        return list(csv.DictReader(f))\n"
        "\n"
        "def _input_rows():\n"
        "    with open(\"uploads/sample_input.csv\", newline=\"\") as f:\n"
        "        return list(csv.DictReader(f))\n"
        "\n"
        "def _report():\n"
        "    with open(\"reports/validation_report.md\", encoding=\"utf-8\") as f:\n"
        "        return f.read()\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    _run_agent()\n"
        "    assert len(_rows()) == len(_input_rows())\n"
        "\n"
        "def test_category_presence():\n"
        "    _run_agent()\n"
        "    rows = _rows()\n"
        "    assert rows\n"
        "    assert all(row[\"category\"] in _ALLOWED for row in rows)\n"
        "\n"
        "def test_required_columns():\n"
        "    _run_agent()\n"
        "    rows = _rows()\n"
        "    required = {\"txn_id\", \"date\", \"amount\", \"description\", \"counterparty\", \"account\", \"category\", \"rule_matched\", \"rule_used\", \"confidence\"}\n"
        "    assert required.issubset(rows[0].keys())\n"
        "\n"
        "def test_summary_report_written():\n"
        "    _run_agent()\n"
        "    report = _report()\n"
        "    assert report.strip()\n"
        "    assert report.lstrip().startswith(\"#\")\n"
    )


def _pytest_row_count_mismatch_test_source() -> str:
    return (
        "import csv\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "_CMD = [\n"
        "    sys.executable,\n"
        "    \"generated/agent.py\",\n"
        "    \"--input\",\n"
        "    \"uploads/sample_input.csv\",\n"
        "    \"--contract\",\n"
        "    \"generated/author_output_contract.json\",\n"
        "    \"--row-output\",\n"
        "    \"outputs/output.csv\",\n"
        "    \"--report-path\",\n"
        "    \"reports/validation_report.md\",\n"
        "]\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"uploads/sample_input.csv\", newline=\"\") as f:\n"
        "        input_rows = list(csv.reader(f))\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        output_rows = list(csv.DictReader(f))\n"
        "    assert len(input_rows) == len(output_rows)\n"
        "\n"
        "def test_required_columns():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        rows = list(csv.DictReader(f))\n"
        "    required = {\"txn_id\", \"date\", \"amount\", \"description\", \"counterparty\", \"account\", \"category\", \"rule_matched\", \"rule_used\", \"confidence\"}\n"
        "    assert rows\n"
        "    assert required.issubset(rows[0].keys())\n"
    )


def _pytest_missing_import_repair_source() -> str:
    return (
        "import csv\n"
        "import sys\n"
        "\n"
        "_CMD = [\n"
        "    sys.executable,\n"
        "    \"generated/agent.py\",\n"
        "    \"--input\",\n"
        "    \"uploads/sample_input.csv\",\n"
        "    \"--contract\",\n"
        "    \"generated/author_output_contract.json\",\n"
        "    \"--row-output\",\n"
        "    \"outputs/output.csv\",\n"
        "    \"--report-path\",\n"
        "    \"reports/validation_report.md\",\n"
        "]\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"uploads/sample_input.csv\", newline=\"\") as f:\n"
        "        input_rows = list(csv.DictReader(f))\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        output_rows = list(csv.DictReader(f))\n"
        "    assert len(input_rows) == len(output_rows)\n"
        "\n"
        "def test_required_columns():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        rows = list(csv.DictReader(f))\n"
        "    required = {\"txn_id\", \"date\", \"amount\", \"description\", \"counterparty\", \"account\", \"category\", \"rule_matched\", \"rule_used\", \"confidence\"}\n"
        "    assert rows\n"
        "    assert required.issubset(rows[0].keys())\n"
    )


def _pytest_invented_sign_rule_test_source() -> str:
    return (
        "import csv\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "_CMD = [\n"
        "    sys.executable,\n"
        "    \"generated/agent.py\",\n"
        "    \"--input\",\n"
        "    \"uploads/sample_input.csv\",\n"
        "    \"--contract\",\n"
        "    \"generated/author_output_contract.json\",\n"
        "    \"--row-output\",\n"
        "    \"outputs/output.csv\",\n"
        "    \"--report-path\",\n"
        "    \"reports/validation_report.md\",\n"
        "]\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"uploads/sample_input.csv\", newline=\"\") as f:\n"
        "        input_rows = list(csv.DictReader(f))\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        output_rows = list(csv.DictReader(f))\n"
        "    assert len(input_rows) == len(output_rows)\n"
        "\n"
        "def test_required_columns():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        rows = list(csv.DictReader(f))\n"
        "    required = {\"txn_id\", \"date\", \"amount\", \"description\", \"counterparty\", \"account\", \"category\", \"rule_matched\", \"rule_used\", \"confidence\"}\n"
        "    assert rows\n"
        "    assert required.issubset(rows[0].keys())\n"
        "\n"
        "def test_required_non_null_columns():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        rows = list(csv.DictReader(f))\n"
        "    for row in rows:\n"
        "        assert row[\"category\"].strip()\n"
        "        assert row[\"rule_matched\"].strip()\n"
        "        assert row[\"rule_used\"].strip()\n"
        "        assert row[\"confidence\"].strip()\n"
        "\n"
        "def test_business_rule():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        rows = list(csv.DictReader(f))\n"
        "    for row in rows:\n"
        "        if row[\"category\"] == \"Income\":\n"
        "            assert float(row[\"amount\"]) > 0\n"
        "        elif row[\"category\"] in {\"Office Expense\", \"Travel\", \"Subscriptions\", \"Refund\"}:\n"
        "            assert float(row[\"amount\"]) < 0\n"
    )


def _pytest_report_metric_identifier_literalism_source() -> str:
    return (
        "import csv\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "_CMD = [\n"
        "    sys.executable,\n"
        "    \"generated/agent.py\",\n"
        "    \"--input\",\n"
        "    \"uploads/sample_input.csv\",\n"
        "    \"--contract\",\n"
        "    \"generated/author_output_contract.json\",\n"
        "    \"--row-output\",\n"
        "    \"outputs/output.csv\",\n"
        "    \"--report-path\",\n"
        "    \"reports/validation_report.md\",\n"
        "]\n"
        "\n"
        "def _run_agent():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    # Contract requirement: preserve_row_count is true\n"
        "    _run_agent()\n"
        "    with open(\"uploads/sample_input.csv\", newline=\"\") as f:\n"
        "        input_rows = list(csv.DictReader(f))\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        output_rows = list(csv.DictReader(f))\n"
        "    assert len(input_rows) == len(output_rows)\n"
        "\n"
        "def test_required_columns():\n"
        "    # Contract requirement: required_output_columns\n"
        "    _run_agent()\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        rows = list(csv.DictReader(f))\n"
        "    assert rows\n"
        "    required = {\"txn_id\", \"date\", \"amount\", \"description\", \"counterparty\", \"account\", \"category\", \"rule_matched\", \"rule_used\", \"confidence\"}\n"
        "    assert required.issubset(rows[0].keys())\n"
        "\n"
        "def test_category_counts():\n"
        "    # Contract requirement: category_counts\n"
        "    _run_agent()\n"
        "    with open(\"reports/validation_report.md\", encoding=\"utf-8\") as f:\n"
        "        content = f.read()\n"
        "    assert \"category_counts\" in content, \"Category counts not found in validation report\"\n"
        "\n"
        "def test_uncertain_rows_count():\n"
        "    # Contract requirement: uncertain_rows_count\n"
        "    _run_agent()\n"
        "    with open(\"reports/validation_report.md\", encoding=\"utf-8\") as f:\n"
        "        content = f.read()\n"
        "    assert \"uncertain_rows_count\" in content, \"Uncertain rows count not found in validation report\"\n"
    )


def _pytest_semantic_report_heading_test_source() -> str:
    return (
        "import csv\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "_CMD = [\n"
        "    sys.executable,\n"
        "    \"generated/agent.py\",\n"
        "    \"--input\",\n"
        "    \"uploads/sample_input.csv\",\n"
        "    \"--contract\",\n"
        "    \"generated/author_output_contract.json\",\n"
        "    \"--row-output\",\n"
        "    \"outputs/output.csv\",\n"
        "    \"--report-path\",\n"
        "    \"reports/validation_report.md\",\n"
        "]\n"
        "\n"
        "def _run_agent():\n"
        "    result = subprocess.run(_CMD, capture_output=True, text=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "\n"
        "def _rows():\n"
        "    with open(\"outputs/output.csv\", newline=\"\") as f:\n"
        "        return list(csv.DictReader(f))\n"
        "\n"
        "def _normalise(text):\n"
        "    return \" \".join(text.lower().replace(\"_\", \" \").split())\n"
        "\n"
        "def _report():\n"
        "    with open(\"reports/validation_report.md\", encoding=\"utf-8\") as f:\n"
        "        return f.read()\n"
        "\n"
        "def test_row_count_preservation():\n"
        "    # Contract requirement: preserve_row_count is true\n"
        "    _run_agent()\n"
        "    with open(\"uploads/sample_input.csv\", newline=\"\") as f:\n"
        "        input_rows = list(csv.DictReader(f))\n"
        "    assert len(input_rows) == len(_rows())\n"
        "\n"
        "def test_required_columns():\n"
        "    # Contract requirement: required_output_columns\n"
        "    _run_agent()\n"
        "    rows = _rows()\n"
        "    assert rows\n"
        "    required = {\"txn_id\", \"date\", \"amount\", \"description\", \"counterparty\", \"account\", \"category\", \"rule_matched\", \"rule_used\", \"confidence\"}\n"
        "    assert required.issubset(rows[0].keys())\n"
        "\n"
        "def test_category_counts():\n"
        "    # Contract requirement: category_counts\n"
        "    _run_agent()\n"
        "    report = _normalise(_report())\n"
        "    assert \"category counts\" in report\n"
        "    for category in {row['category'] for row in _rows()}:\n"
        "        assert category.lower() in report\n"
        "\n"
        "def test_uncertain_rows_count():\n"
        "    # Contract requirement: uncertain_rows_count\n"
        "    _run_agent()\n"
        "    rows = _rows()\n"
        "    expected = sum(1 for row in rows if float(row['confidence']) < 0.8)\n"
        "    report = _normalise(_report())\n"
        "    assert \"uncertain rows count\" in report\n"
        "    assert str(expected) in report\n"
    )


def _session_like_positive_office_expense_agent_source() -> str:
    return (
        "import csv\n"
        "import json\n"
        "\n"
        "def categorize_transaction(description, counterparty):\n"
        "    if \"income\" in description.lower() or \"refund\" in description.lower():\n"
        "        return \"Income\", \"Income Rule\", 1.0\n"
        "    elif \"office\" in description.lower() or \"rent\" in description.lower():\n"
        "        return \"Office Expense\", \"Office Rule\", 0.95\n"
        "    elif \"travel\" in description.lower() or \"flight\" in description.lower():\n"
        "        return \"Travel\", \"Travel Rule\", 0.85\n"
        "    elif \"subscription\" in description.lower() or \"service\" in description.lower():\n"
        "        return \"Subscriptions\", \"Subscription Rule\", 0.90\n"
        "    else:\n"
        "        return \"Uncategorised\", \"No matching rule\", 0.0\n"
        "\n"
        "def process_csv(input_path, contract_path, output_path, report_path):\n"
        "    with open(contract_path, 'r') as f:\n"
        "        contract = json.load(f)\n"
        "    category_counts = {}\n"
        "    uncertain_rows_count = 0\n"
        "    with open(input_path, 'r') as infile, open(output_path, 'w', newline='') as outfile:\n"
        "        reader = csv.DictReader(infile)\n"
        "        writer = csv.DictWriter(outfile, fieldnames=contract['output_columns'])\n"
        "        writer.writeheader()\n"
        "        for row in reader:\n"
        "            category, rule_matched, confidence = categorize_transaction(row['description'], row['counterparty'])\n"
        "            row['category'] = category\n"
        "            row['rule_matched'] = rule_matched\n"
        "            row['rule_used'] = rule_matched\n"
        "            row['confidence'] = confidence\n"
        "            writer.writerow(row)\n"
        "            category_counts[category] = category_counts.get(category, 0) + 1\n"
        "            if confidence < 0.8:\n"
        "                uncertain_rows_count += 1\n"
        "    with open(report_path, 'w') as reportfile:\n"
        "        reportfile.write(\"# Validation Report\\n\")\n"
        "        reportfile.write(\"## Category Counts\\n\")\n"
        "        for category, count in category_counts.items():\n"
        "            reportfile.write(f\"- {category}: {count}\\n\")\n"
        "        reportfile.write(\"\\n## Uncertain Rows Count\\n\")\n"
        "        reportfile.write(f\"{uncertain_rows_count}\\n\")\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    import argparse\n"
        "    parser = argparse.ArgumentParser(description=\"Categorise bank transactions.\")\n"
        "    parser.add_argument(\"--input\", required=True)\n"
        "    parser.add_argument(\"--contract\", required=True)\n"
        "    parser.add_argument(\"--row-output\", required=True)\n"
        "    parser.add_argument(\"--report-path\", required=True)\n"
        "    args = parser.parse_args()\n"
        "    process_csv(args.input, args.contract, args.row_output, args.report_path)\n"
    )


def test_generated_code_execution_failure_uses_bounded_model_repair(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    base_script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    repair_response = _response(
        "execution-repair-valid-agent",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": _agent_source_from(base_script[2]),
                }
            ],
            "notes": "Replace broken generated agent.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_agent_source(_bad_agent_source())
    script.append(repair_response)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=FakeModelClient(script=script),
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    purposes = [
        event.payload.get("purpose")
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    ]
    assert purposes.count("execution_repair") == 1
    assert (workspace / "generated" / "repairs" / "attempt_1" / "agent.py").is_file()
    assert (workspace / "generated" / "agent.py").read_text(encoding="utf-8") == _agent_source_from(
        base_script[2]
    )
    assert (workspace / "outputs" / "output.csv").is_file()


def test_runtime_repair_invalid_candidates_do_not_overwrite_canonical_agent(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    broken_source = _name_error_agent_source()
    invalid_candidate = _syntax_broken_agent_source()
    repair_response = _response(
        "execution-repair-invalid-syntax",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": invalid_candidate,
                }
            ],
            "notes": "Broken syntax.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_agent_source(broken_source)
    script.extend([repair_response, repair_response])
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    assert (workspace / "generated" / "agent.py").read_text(encoding="utf-8") == broken_source
    assert (
        workspace / "generated" / "repairs" / "attempt_1" / "agent.py"
    ).read_text(encoding="utf-8") == invalid_candidate
    assert (
        workspace / "generated" / "repairs" / "attempt_2" / "agent.py"
    ).read_text(encoding="utf-8") == invalid_candidate

    events = event_log.read_all(sid)
    executions = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "custom_workflow_execution"
    ]
    assert len(executions) == 1
    assert executions[0]["repair_attempt"] == 0

    prompt_1 = json.loads(model.calls[4]["messages"][0]["content"][0]["text"])
    prompt_2 = json.loads(model.calls[5]["messages"][0]["content"][0]["text"])
    assert prompt_1["failure_kind"] == "execution"
    assert "NameError" in prompt_1["failure_detail"]
    current_1 = next(item for item in prompt_1["current_files"] if item["path"] == "generated/agent.py")
    current_2 = next(item for item in prompt_2["current_files"] if item["path"] == "generated/agent.py")
    assert current_1["content"] == broken_source
    assert prompt_2["failure_kind"] == "syntax"
    assert "Repair candidate failed Python syntax preflight before promotion." in prompt_2["failure_detail"]
    assert "generated/repairs/attempt_1/agent.py" in prompt_2["failure_detail"]
    assert current_2["content"] == broken_source

    failed = next(event for event in events if event.kind == EventKind.WORKFLOW_FAILED)
    assert "NameError" in failed.payload["technical_detail"]
    assert "Repair candidate failed Python syntax preflight before promotion." in failed.payload["technical_detail"]

    summary = (workspace / "reports" / "model_authoring_summary.md").read_text(
        encoding="utf-8"
    )
    assert "## Repair candidates" in summary
    assert "Attempt 1: rejected" in summary
    assert "Attempt 2: rejected" in summary


def test_agent_writing_contract_path_instead_of_output_csv_fails_closed(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    bad_repair = _response(
        "missing-output-repair-still-invalid",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": _syntax_broken_agent_source(),
                }
            ],
            "notes": "Still invalid.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_agent_source(_contract_path_output_agent_source())
    script.extend([bad_repair, bad_repair])
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    assert not (workspace / "outputs" / "output.csv").exists()
    assert (workspace / "reports" / "validation_report.md").is_file()
    contract_path = workspace / "generated" / "author_output_contract.json"
    with pytest.raises(json.JSONDecodeError):
        json.loads(contract_path.read_text(encoding="utf-8"))
    csv_paths = sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*.csv")
        if path.is_file()
    )
    assert csv_paths == ["uploads/sample_input.csv"]
    assert contract_path.is_file()

    prompt_1 = json.loads(model.calls[4]["messages"][0]["content"][0]["text"])
    assert prompt_1["failure_kind"] == "missing_artifacts"
    assert "missing_required_outputs=outputs/output.csv" in prompt_1["failure_detail"]
    assert "--contract generated/author_output_contract.json" in prompt_1["failure_detail"]
    assert "--row-output outputs/output.csv" in prompt_1["failure_detail"]
    assert "workspace_artifact_tree" in prompt_1["failure_detail"]
    assert prompt_1["required_cli_interface"]["arguments"]["--contract"] == "generated/author_output_contract.json"
    assert prompt_1["required_cli_interface"]["arguments"]["--row-output"] == "outputs/output.csv"
    assert "Never write CSV rows" in prompt_1["required_cli_interface"]["semantics"]["--contract"]
    assert prompt_1["repair_contract"]["allowed_files"] == ["generated/agent.py"]
    assert prompt_1["repair_contract"]["required_files"] == ["generated/agent.py"]
    assert [item["path"] for item in prompt_1["current_files"]] == ["generated/agent.py"]


def test_author_generated_code_failure_message_prefers_exception_over_artifact_tree() -> None:
    detail = (
        "Generated agent exited 1.\n"
        "missing_required_outputs=outputs/output.csv, reports/validation_report.md\n"
        "stderr: Traceback (most recent call last):\n"
        "  File \"/tmp/work/generated/agent.py\", line 74, in main\n"
        "    output_columns = [col['name'] for col in contract['output_columns']]\n"
        "TypeError: string indices must be integers, not 'str'\n"
        "workspace_artifact_tree:\n"
        "generated/model_responses/contract_planning.txt\n"
        "generated/model_responses/execution_repair_2.txt\n"
        "generated/tests/test_agent.py"
    )

    message = _author_generated_code_failure_message(detail)

    assert message == (
        "Generated agent exited 1: TypeError: string indices must be integers, not 'str'"
    )
    assert "generated/model_responses" not in message


def test_execution_repair_prompt_includes_required_artifact_schema_and_agent_only_scope(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "agent.py").write_text(_bad_agent_source(), encoding="utf-8")
    outputs = workspace / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "output.csv").write_text(
        "txn_id,date,amount,description,counterparty,account,category,rule_matched,rule_used,confidence\n",
        encoding="utf-8",
    )
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/sample_input.csv",
            "input_format": "csv",
            "normalized_input_path": "uploads/sample_input.csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [
                {
                    "path": "reports/validation_report.md",
                    "description": "Validation report",
                    "required_columns": [
                        "category_counts",
                        "uncertain_rows",
                        "human_review_rows",
                    ],
                }
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": [
                        "txn_id",
                        "issue_flag",
                        "rule_matched",
                        "rule_used",
                        "confidence",
                    ],
                }
            ],
            "input_columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
            "output_columns": [
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
            "required_output_columns": ["category", "rule_matched", "rule_used", "confidence"],
            "output_column_semantics": [
                {
                    "name": "category",
                    "description": "Assigned category",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty category for every output row.",
                    "fallback_value_semantics": "Use an explicit uncategorised label when no rule matches.",
                },
                {
                    "name": "rule_matched",
                    "description": "Matched rule explanation",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule explanation for every row.",
                    "fallback_value_semantics": "Use an explicit no-match explanation when no rule matches.",
                },
                {
                    "name": "rule_used",
                    "description": "Rule audit label",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule audit label for every row.",
                    "fallback_value_semantics": "Use an explicit default rule label when no rule matches.",
                },
                {
                    "name": "confidence",
                    "description": "Confidence score",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty confidence value for every row.",
                    "fallback_value_semantics": "Use an explicit low-confidence value when uncertain.",
                },
            ],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                },
                {
                    "name": "exceptions",
                    "output_path": "outputs/exceptions.csv",
                    "required": True,
                },
            ],
        }
    )
    model = FakeModelClient(
        script=[
            _response(
                "execution-repair-agent-only",
                {
                    "files": [
                        {
                            "path": "generated/agent.py",
                            "content": _bad_agent_source(),
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)

    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_execution_repair_prompt",
            model_client=model,
            workspace=workspace,
            user_description="Categorise these bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 200,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="missing_artifacts",
            failure_detail=(
                "Generated agent exited 1.\n"
                "command=python generated/agent.py --input uploads/sample_input.csv --contract "
                "generated/author_output_contract.json --row-output outputs/output.csv "
                "--report-path reports/validation_report.md\n"
                "missing_required_outputs=outputs/exceptions.csv\n"
                "contract_input_modified=False\n"
                "contract_json_valid_after_execution=True\n"
                "stderr: ValueError: dict contains fields not in fieldnames: 'issue_flag'\n"
                "workspace_artifact_tree:\noutputs/output.csv\ngenerated/agent.py"
            ),
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert prompt["repair_contract"]["allowed_files"] == ["generated/agent.py"]
    assert prompt["repair_contract"]["required_files"] == ["generated/agent.py"]
    assert [item["path"] for item in prompt["current_files"]] == ["generated/agent.py"]
    assert prompt["output_contract_summary"]["required_output_paths"] == [
        "outputs/output.csv",
        "reports/validation_report.md",
        "outputs/exceptions.csv",
    ]
    artifact_paths = [item["path"] for item in prompt["output_contract_summary"]["required_artifacts"]]
    assert artifact_paths == [
        "outputs/output.csv",
        "reports/validation_report.md",
        "outputs/exceptions.csv",
    ]
    exception_artifact = prompt["output_contract_summary"]["required_artifacts"][2]
    assert exception_artifact["required_columns"] == [
        "txn_id",
        "issue_flag",
        "rule_matched",
        "rule_used",
        "confidence",
    ]
    assert prompt["existing_artifact_context"]["row_output_header"] == [
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
    ]


def test_execution_repair_prompt_includes_missing_and_produced_required_artifact_context(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(
        _FIXTURE_VENDOR_PAYMENT_MISSING_REQUIRED_ARTIFACTS.read_text(encoding="utf-8")
    )
    contract = AuthorOutputContract.model_validate(fixture["author_output_contract"])
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "agent.py").write_text(_bad_agent_source(), encoding="utf-8")
    outputs = workspace / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "output.csv").write_text(
        "vendor_id,vendor_name,invoice_id,invoice_date,due_date,amount,currency,payment_terms,approval_status,bank_account_present,tax_form_on_file,hold_flag,payment_method,category,notes,payment_status,payment_ready,exception_reason,recommended_payment_date\n",
        encoding="utf-8",
    )
    reports = workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "validation_report.md").write_text("# Validation report\n", encoding="utf-8")
    model = FakeModelClient(
        script=[
            _response(
                "execution-repair-missing-required-artifacts",
                {
                    "files": [
                        {
                            "path": "generated/agent.py",
                            "content": _bad_agent_source(),
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)

    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_execution_repair_prompt_vendor_missing_artifacts",
            model_client=model,
            workspace=workspace,
            user_description="Prepare vendor payments for finance review.",
            schema_profile={
                "columns": contract.input_columns,
                "row_count": 10,
                "upload_format": "csv",
                "agent_input_path": "uploads/vendor_payment_preparation.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="missing_artifacts",
            failure_detail=(
                "Generated agent exited 0.\n"
                "command=python generated/agent.py --input uploads/vendor_payment_preparation.csv --contract "
                "generated/author_output_contract.json --row-output outputs/output.csv "
                "--report-path reports/validation_report.md\n"
                "missing_required_outputs=outputs/summary_by_currency_and_method.csv, "
                "outputs/exceptions.csv\n"
                "contract_input_modified=False\n"
                "contract_json_valid_after_execution=True\n"
                "stderr: \n"
                "workspace_artifact_tree:\noutputs/output.csv\nreports/validation_report.md\ngenerated/agent.py"
            ),
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert prompt["runtime_failure_context"]["missing_required_outputs"] == [
        "outputs/summary_by_currency_and_method.csv",
        "outputs/exceptions.csv",
    ]
    assert prompt["output_contract_summary"]["required_output_paths"] == [
        "outputs/output.csv",
        "outputs/summary_by_currency_and_method.csv",
        "outputs/exceptions.csv",
    ]
    assert prompt["existing_artifact_context"]["produced_artifact_paths"] == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    assert prompt["existing_artifact_context"]["produced_required_artifact_paths"] == [
        "outputs/output.csv"
    ]
    assert prompt["existing_artifact_context"]["missing_required_artifact_paths"] == [
        "outputs/summary_by_currency_and_method.csv",
        "outputs/exceptions.csv",
    ]
    artifact_requirements = prompt["repair_contract"]["artifact_repair_requirements"]
    safety_requirements = prompt["repair_contract"]["safety_requirements"]
    assert any(
        "Use runtime_failure_context.missing_required_outputs as the exact missing path list."
        in item
        for item in artifact_requirements
    )
    assert any(
        "Use output_contract_summary.required_output_paths as the full required output list."
        in item
        for item in artifact_requirements
    )
    assert any(
        "Use existing_artifact_context.produced_artifact_paths as the produced artifact list."
        in item
        for item in artifact_requirements
    )
    assert any("Do not remove outputs from the contract" in item for item in artifact_requirements)
    assert any(
        "If a required exception CSV has zero matching rows, still create it with the required header row."
        in item
        for item in artifact_requirements
    )
    assert any(
        "If a required summary CSV is missing, compute it using the contract's grouping keys (summary_group_keys) and summary metrics (summary_metrics) and write it to the required path."
        in item
        for item in artifact_requirements
    )
    assert any("Do not introduce eval(), exec(), compile()" in item for item in safety_requirements)
    assert any("The repaired generated/agent.py must pass the same static safety scan" in item for item in safety_requirements)


def test_execution_repair_prompt_includes_tuple_unpack_arity_requirements(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_TUPLE_ARITY_FAILURE.read_text(encoding="utf-8")
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "agent.py").write_text(
        fixture["generated_agent_source"],
        encoding="utf-8",
    )
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/bank_transaction_categorisation_demo.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["reports/validation_report.md"],
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
            "output_columns": fixture["expected_assigned_output_columns"],
            "required_output_columns": fixture["expected_assigned_output_columns"],
            "output_column_semantics": [
                {
                    "name": "category",
                    "description": "Assigned category",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Assign one category.",
                    "fallback_value_semantics": "Other",
                },
                {
                    "name": "rule_matched",
                    "description": "Matched rule",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit one rule explanation.",
                    "fallback_value_semantics": "No clear rule matched.",
                },
                {
                    "name": "rule_used",
                    "description": "Rule label",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit one rule label.",
                    "fallback_value_semantics": "No clear rule used.",
                },
                {
                    "name": "confidence_score",
                    "description": "Confidence score",
                    "producer_kind": "other",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit one confidence score.",
                    "fallback_value_semantics": "0.50",
                },
                {
                    "name": "review_required",
                    "description": "Review flag",
                    "producer_kind": "exception_flag",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit one review flag.",
                    "fallback_value_semantics": "false",
                },
            ],
            "requested_deliverables": [
                "outputs/output.csv",
                "reports/validation_report.md",
            ],
            "allowed_enums": {
                "category": [
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
            },
        }
    )
    model = FakeModelClient(
        script=[
            _response(
                "execution-repair-tuple-arity",
                {
                    "files": [
                        {
                            "path": "generated/agent.py",
                            "content": fixture["generated_agent_source"],
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)

    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_execution_repair_prompt_tuple_arity",
            model_client=model,
            workspace=workspace,
            user_description=fixture["user_description"],
            schema_profile={
                "columns": contract.input_columns,
                "row_count": 18,
                "upload_format": "csv",
                "agent_input_path": "uploads/bank_transaction_categorisation_demo.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="execution",
            failure_detail=fixture["execution_error"],
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    context = prompt["runtime_failure_context"]["tuple_unpack_arity"]
    assert context["expected_unpack_values"] == 5
    assert context["observed_return_values"] == 4
    assert context["helper_name"] == "categorize_transaction"
    requirements = prompt["repair_contract"]["helper_return_arity_repair_requirements"]
    assert any("returned the wrong number of values" in item for item in requirements)
    assert any("expects 5 values from categorize_transaction" in item for item in requirements)
    assert any("Do not reduce the unpack target or remove contract-required output columns" in item for item in requirements)
    assert any("return it twice" in item for item in requirements)


def test_execution_repair_prompt_includes_dictwriter_fieldnames_requirements(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_EXCEPTION_CSV_FIELDNAMES_FAILURE.read_text(encoding="utf-8")
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "agent.py").write_text(
        fixture["generated_agent_source"],
        encoding="utf-8",
    )
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/bank_transaction_categorisation_demo.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["reports/summary_by_category.md"],
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
                    "path": fixture["writer_target_path"],
                    "required_columns": fixture["writer_fieldnames"],
                }
            ],
        }
    )
    model = FakeModelClient(
        script=[
            _response(
                "execution-repair-dictwriter-fieldnames",
                {
                    "files": [
                        {
                            "path": "generated/agent.py",
                            "content": fixture["generated_agent_source"],
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)

    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_execution_repair_prompt_dictwriter_fieldnames",
            model_client=model,
            workspace=workspace,
            user_description=fixture["user_description"],
            schema_profile={
                "columns": contract.input_columns,
                "row_count": 18,
                "upload_format": "csv",
                "agent_input_path": "uploads/bank_transaction_categorisation_demo.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="execution",
            failure_detail=fixture["execution_error"],
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    context = prompt["runtime_failure_context"]["dictwriter_fieldnames"]
    assert context["configured_fieldnames"] == fixture["writer_fieldnames"]
    assert "confidence_score" in context["extra_row_keys"]
    requirements = prompt["repair_contract"]["dictwriter_fieldnames_repair_requirements"]
    assert any("dict contains fields not in fieldnames" in item.lower() or "fieldnames" in item for item in requirements)
    assert any("projecting each row to the declared fieldnames" in item for item in requirements)
    assert any("Do not delete exception_rows" in item for item in requirements)
    assert any("still create the required exception CSV with its header row" in item for item in requirements)
    artifact_requirements = prompt["repair_contract"].get("artifact_repair_requirements", [])
    if artifact_requirements:
        assert any("required exception CSV has zero matching rows" in item for item in artifact_requirements)


def test_execution_repair_prompt_includes_bundled_bank_reference_constraints(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(
        _FIXTURE_BANK_DEMO_TUPLE_ARITY_FAILURE.read_text(encoding="utf-8")
    )
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "agent.py").write_text(
        fixture["generated_agent_source"],
        encoding="utf-8",
    )
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    model = FakeModelClient(
        script=[
            _response(
                "execution-repair-bank-reference",
                {
                    "files": [
                        {
                            "path": "generated/agent.py",
                            "content": fixture["generated_agent_source"],
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)
    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_execution_repair_prompt_bank_reference",
            model_client=model,
            workspace=workspace,
            user_description=fixture["user_description"],
            schema_profile=_bank_reference_schema_profile(),
            contract=contract,
            failure_kind="execution",
            failure_detail=fixture["execution_error"],
            attempt=1,
            template_hint="bank_categoriser",
            settings=Settings(author_enable_bank_reference_scaffold=True),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    reference = prompt["repair_contract"]["reference_sample_constraints"]
    assert reference["template"] == "bank_categoriser"
    assert reference["required_categories"] == list(
        _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES
    )
    assert any(
        "Revenue=3, Payroll=1, Software=4" in item
        for item in reference["repair_requirements"]
    )
    assert any(
        row["transaction_id"] == "BTX-0018" and row["expected_category"] == "Other"
        for row in reference["expected_rows"]
    )


def test_stage_bundled_bank_reference_golden_copies_expected_output(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)

    assert stage_bundled_bank_reference_golden(workspace=workspace) is True
    staged = workspace / "evals" / "golden_output.csv"
    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == _FIXTURE_BANK_DEMO_EXPECTED_OUTPUT.read_text(
        encoding="utf-8"
    )


def test_apply_bundled_bank_reference_golden_policy_overrides_skipped_requirement() -> None:
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    ).model_copy(
        update={
            "golden_comparison_requirement": "skipped",
            "golden_output_path": None,
            "skipped_checks": ["golden_output"],
        }
    )
    fixed = apply_bundled_bank_reference_golden_policy(contract)
    assert fixed.golden_comparison_requirement == "required"
    assert fixed.golden_output_path == "evals/golden_output.csv"
    assert "golden_output" not in fixed.skipped_checks


def test_bundled_bank_reference_contract_validation_runs_golden_comparison(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    output_path = workspace / "outputs" / "output.csv"
    report_path = workspace / "reports" / "validation_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _FIXTURE_BANK_DEMO_EXPECTED_OUTPUT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report_path.write_text("# Validation Report\n\nCategory summary.\n", encoding="utf-8")
    assert stage_bundled_bank_reference_golden(workspace=workspace) is True

    contract = apply_bundled_bank_reference_golden_policy(
        _bundled_bank_reference_contract_scaffold(
            schema_profile=_bank_reference_schema_profile()
        ).model_copy(
            update={
                "golden_comparison_requirement": "skipped",
                "golden_output_path": None,
                "skipped_checks": ["golden_output"],
            }
        )
    )
    report, _warnings = validate_against_contract(
        session_id=sid,
        workspace=workspace,
        contract=contract,
        test_results=None,
        golden_ignore_columns=("rule_matched", "rule_used", "confidence_score"),
    )

    golden_check = next(
        check for check in report.checks if check.layer.value == "golden_output"
    )
    assert golden_check.skipped is not True
    assert golden_check.passed is True


def test_bundled_bank_reference_validation_checks_accept_expected_output_fixture(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    output_path = workspace / "outputs" / "output.csv"
    report_path = workspace / "reports" / "validation_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _FIXTURE_BANK_DEMO_EXPECTED_OUTPUT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report_path.write_text(
        "# Validation Report\n\nCategory summary and validation evidence.\n",
        encoding="utf-8",
    )
    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )

    checks = _bank_reference_sample_validation_checks(
        workspace=workspace,
        contract=contract,
    )

    assert len(checks) == 3
    assert all(check.passed is True for check in checks)


def test_contract_validation_failure_detail_includes_bundled_bank_demo_mismatches(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    output_path = workspace / "outputs" / "output.csv"
    report_path = workspace / "reports" / "validation_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    rows = _FIXTURE_BANK_DEMO_EXPECTED_OUTPUT.read_text(encoding="utf-8").splitlines()
    rows[7] = rows[7].replace(",Bank Fees,", ",Other,", 1)
    output_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    report_path.write_text("# Validation Report\n\nNon-empty.\n", encoding="utf-8")

    contract = _bundled_bank_reference_contract_scaffold(
        schema_profile=_bank_reference_schema_profile()
    )
    report = ValidationReport(
        session_id=sid,
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.SEMANTIC_RULES,
                name="Bundled bank demo expected classifications",
                passed=False,
                evidence="BTX-0007 mismatch",
            )
        ],
    )

    detail = _contract_validation_failure_detail(
        workspace=workspace,
        contract=contract,
        report=report,
        execution_command_text="python generated/agent.py ...",
        include_bank_reference_detail=True,
    )

    assert "bundled_bank_demo_expected_category_counts=" in detail
    assert json.dumps(_BUNDLED_BANK_REFERENCE_EXPECTED_CATEGORY_COUNTS, sort_keys=True) in detail
    assert "bundled_bank_demo_actual_category_counts=" in detail
    assert "\"transaction_id\": \"BTX-0007\"" in detail
    assert "\"expected_category\": \"Bank Fees\"" in detail
    assert "\"actual_category\": \"Other\"" in detail


def test_author_generated_code_failure_message_surfaces_dictwriter_fieldnames_error() -> None:
    from agentforge.orchestrator.author_custom_build import _author_generated_code_failure_message

    fixture = json.loads(
        _FIXTURE_BANK_DEMO_EXCEPTION_CSV_FIELDNAMES_FAILURE.read_text(encoding="utf-8")
    )
    message = _author_generated_code_failure_message(fixture["execution_error"])
    assert "dict contains fields not in fieldnames" in message
    assert "extra keys=" in message
    assert "Expand DictWriter fieldnames or project each row" in message


def test_execution_repair_rejects_test_file_rewrites_before_pytest(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated" / "tests"
    generated.mkdir(parents=True, exist_ok=True)
    (workspace / "generated" / "agent.py").write_text(_bad_agent_source(), encoding="utf-8")
    (generated / "test_agent.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/sample_input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "category"],
            "required_output_columns": ["category"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    model = FakeModelClient(
        script=[
            _response(
                "execution-repair-illegal-test-rewrite",
                {
                    "files": [
                        {"path": "generated/agent.py", "content": _bad_agent_source()},
                        {
                            "path": "generated/tests/test_agent.py",
                            "content": "def test_changed():\n    assert False\n",
                        },
                    ],
                    "notes": "Incorrectly rewrites tests before pytest.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)

    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_execution_repair_scope",
            model_client=model,
            workspace=workspace,
            user_description="Categorise these bank transactions.",
            schema_profile={
                "columns": ["txn_id"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="execution",
            failure_detail="RuntimeError: broken agent",
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    assert "wrong replacement file set" in (result.message or "")
    assert "unexpected_paths=generated/tests/test_agent.py" in (result.technical_detail or "")


def test_contract_validation_repair_prompt_includes_null_counts_and_samples(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    generated = workspace / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "agent.py").write_text(_validation_null_agent_source(), encoding="utf-8")
    outputs = workspace / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "output.csv").write_text(
        (
            "txn_id,date,amount,description,counterparty,account,category,rule_matched,rule_used,confidence\n"
            "txn-001,2024-01-01,12.00,desk supplies,ACME,Main,Office Expense,,,1.0\n"
            "txn-002,2024-01-02,50.00,monthly subscription,ACME,Main,Subscriptions,,,1.0\n"
        ),
        encoding="utf-8",
    )
    reports = workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "validation_report.md").write_text(
        "# Validation Report\n\nRows requiring review are summarised here.\n",
        encoding="utf-8",
    )
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorisation",
            "build_mode": "llm_custom",
            "input_file": "uploads/sample_input.csv",
            "input_format": "csv",
            "normalized_input_path": "uploads/sample_input.csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": ["reports/summary_report.md"],
            "input_columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
            "output_columns": [
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
            "required_output_columns": ["category", "rule_matched", "rule_used", "confidence"],
            "output_column_semantics": [
                {
                    "name": "category",
                    "description": "Assigned category",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty category for every output row.",
                    "fallback_value_semantics": "Use an explicit uncategorised label when no rule matches.",
                },
                {
                    "name": "rule_matched",
                    "description": "Matched rule explanation",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule explanation for every row.",
                    "fallback_value_semantics": "Use an explicit no-match explanation when no rule matches.",
                },
                {
                    "name": "rule_used",
                    "description": "Rule audit label",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty rule audit label for every row.",
                    "fallback_value_semantics": "Use an explicit default rule label when no rule matches.",
                },
                {
                    "name": "confidence",
                    "description": "Confidence score",
                    "producer_kind": "classification",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Emit a non-empty confidence value for every row.",
                    "fallback_value_semantics": "Use an explicit low-confidence value when uncertain.",
                },
            ],
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
    report = ValidationReport(
        session_id=sid,
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.REQUIRED_COLUMNS,
                name="Required columns non-null",
                passed=False,
                evidence="null cells: {'rule_matched': 2, 'rule_used': 2}",
                detail=None,
                hint_if_failed=None,
                skipped=False,
            )
        ],
    )
    failure_detail = _contract_validation_failure_detail(
        workspace=workspace,
        contract=contract,
        report=report,
        execution_command_text=(
            "python generated/agent.py --input uploads/sample_input.csv --contract "
            "generated/author_output_contract.json --row-output outputs/output.csv "
            "--report-path reports/validation_report.md"
        ),
    )
    assert "required_column_null_counts={'rule_matched': 2, 'rule_used': 2}" in failure_detail
    assert "\"_missing_required_columns\": \"rule_matched,rule_used\"" in failure_detail
    assert "command=python generated/agent.py --input uploads/sample_input.csv" in failure_detail

    model = FakeModelClient(
        script=[
            _response(
                "contract-validation-repair",
                {
                    "files": [
                        {
                            "path": "generated/agent.py",
                            "content": _validation_null_agent_source(),
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)
    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_contract_validation_repair_prompt",
            model_client=model,
            workspace=workspace,
            user_description="Categorise these bank transactions.",
            schema_profile={
                "columns": ["txn_id", "date", "amount", "description", "counterparty", "account"],
                "row_count": 2,
                "upload_format": "csv",
                "agent_input_path": "uploads/sample_input.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="contract_validation",
            failure_detail=failure_detail,
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert prompt["repair_contract"]["allowed_files"] == ["generated/agent.py"]
    assert prompt["repair_contract"]["required_files"] == ["generated/agent.py"]
    assert [item["path"] for item in prompt["current_files"]] == ["generated/agent.py"]
    assert "required_column_null_counts={'rule_matched': 2, 'rule_used': 2}" in prompt["failure_detail"]
    assert "\"_missing_required_columns\": \"rule_matched,rule_used\"" in prompt["failure_detail"]
    assert prompt["required_cli_interface"]["arguments"]["--report-path"] == (
        "reports/validation_report.md"
    )
    assert prompt["output_contract_summary"]["required_output_columns"] == [
        "category",
        "rule_matched",
        "rule_used",
        "confidence",
    ]
    requirements = prompt["repair_contract"]["validation_repair_requirements"]
    assert any("Do not modify generated/tests/test_agent.py" in item for item in requirements)
    assert any("required and non-null" in item for item in requirements)
    assert any("empty strings, and whitespace-only strings" in item for item in requirements)
    assert any("Inspect every branch" in item for item in requirements)
    assert any("final per-row normalization" in item for item in requirements)
    semantics = prompt["output_contract_summary"]["required_output_column_semantics"]
    assert [item["name"] for item in semantics] == [
        "category",
        "rule_matched",
        "rule_used",
        "confidence",
    ]


def test_pytest_repair_prompt_includes_failure_context_and_can_fix_invalid_generated_tests(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    repair_response = _response(
        "pytest-repair-fix-tests",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_contract_backed_test_source(),
                }
            ],
            "notes": "Repair invalid pytest assertions using contract-backed checks.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_test_source(_pytest_header_blind_test_source())
    script.append(repair_response)
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert (workspace / "generated" / "tests" / "test_agent.py").read_text(
        encoding="utf-8"
    ) == _pytest_contract_backed_test_source()
    assert not (workspace / "test_agent.py").exists()

    prompt = json.loads(model.calls[4]["messages"][0]["content"][0]["text"])
    assert prompt["failure_kind"] == "pytest"
    assert "test_category_presence" in prompt["failure_detail"]
    assert "test_summary_report" in prompt["failure_detail"]
    assert "assert 'category' in" in prompt["failure_detail"]
    assert "Validation report explaining rules and counts" in prompt["failure_detail"]
    assert (
        "output_csv_header=txn_id,date,amount,description,counterparty,account,category,rule_matched,rule_used,confidence"
        in prompt["failure_detail"]
    )
    assert prompt["repair_contract"]["preferred_files"][0] == "generated/tests/test_agent.py"
    assert (
        "Do not treat the CSV header row as data."
        in prompt["repair_contract"]["pytest_repair_requirements"]
    )
    assert (
        "Do not require exact literal report wording unless the contract explicitly requires that exact text."
        in prompt["repair_contract"]["pytest_repair_requirements"]
    )
    assert (
        "If you use csv.DictReader, import csv explicitly."
        in prompt["repair_contract"]["pytest_repair_requirements"]
    )
    assert (
        "If you use sys.executable, import sys explicitly."
        in prompt["repair_contract"]["pytest_repair_requirements"]
    )
    assert (
        "every referenced module has a matching import"
        in " ".join(prompt["repair_contract"]["pytest_repair_requirements"])
    )
    assert (
        "Do not invent domain heuristics or implicit finance assumptions. Do not assert amount sign by category unless the contract explicitly requires that rule."
        in prompt["repair_contract"]["pytest_repair_requirements"]
    )
    assert (
        "internal identifiers unless the contract explicitly requires exact literal text"
        in " ".join(prompt["repair_contract"]["pytest_repair_requirements"])
    )
    assert (
        "normalized human-readable headings or data-derived evidence checks"
        in " ".join(prompt["repair_contract"]["pytest_repair_requirements"])
    )
    assert (
        "Add one short comment above each test naming the contract requirement or artifact it verifies."
        in prompt["repair_contract"]["pytest_repair_requirements"]
    )
    assert "contract-backed assertion" in prompt["instruction"]
    assert "raw snake_case metric identifier" in prompt["instruction"]

    repair_calls = [
        event.payload
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
        and event.payload.get("purpose") == "execution_repair"
    ]
    assert [payload["attempt"] for payload in repair_calls] == [1]
    assert prompt["repair_contract"]["allowed_files"] == ["generated/tests/test_agent.py"]


def test_pytest_repair_prompt_includes_workspace_pathing_requirements(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(_FIXTURE_FAFD3422_PYTEST_PATHING.read_text(encoding="utf-8"))
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    test_path = workspace / "generated" / "tests" / "test_agent.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(fixture["generated_test_source"], encoding="utf-8")
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
    failure_detail = (
        "Generated pytest did not pass any collected tests\n"
        "pytest_command=python -m pytest -v generated/tests\n"
        "AssertionError: Agent failed: /Users/example/bin/python: can't open file "
        "'/private/tmp/pytest-of-user/pytest-4070/test_row_count_preserved0/generated/agent.py': "
        "[Errno 2] No such file or directory\n"
    )
    model = FakeModelClient(
        script=[
            _response(
                "pytest-pathing-repair-prompt",
                {
                    "files": [
                        {
                            "path": "generated/tests/test_agent.py",
                            "content": fixture["workspace_relative_test_source"],
                        }
                    ],
                    "notes": "Inspect prompt only.",
                    "assumptions": [],
                },
            )
        ]
    )
    event_log = EventLog(wm)
    result = asyncio.run(
        repair_generated_author_files(
            session_id=sid,
            event_log=event_log,
            step=0,
            stage_label="test_pytest_pathing_repair_prompt",
            model_client=model,
            workspace=workspace,
            user_description=fixture["user_description"],
            schema_profile={
                "columns": ["expense_id", "amount"],
                "row_count": 1,
                "upload_format": "csv",
                "agent_input_path": "uploads/input.csv",
                "sample_rows": [],
            },
            contract=contract,
            failure_kind="pytest",
            failure_detail=failure_detail,
            attempt=1,
            settings=get_settings(),
            defer_promotion=True,
        )
    )

    assert result.ok is True
    prompt = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert prompt["failure_kind"] == "pytest"
    assert prompt["runtime_failure_context"]["pytest_workspace_pathing"]["uses_tmp_path_fixture"] is True
    pathing_requirements = prompt["repair_contract"]["pytest_workspace_pathing_repair_requirements"]
    assert any("WORKSPACE_ROOT = Path(__file__).resolve().parents[2]" in item for item in pathing_requirements)
    assert any("tmp_path / 'generated/agent.py'" in item for item in pathing_requirements)
    assert any("cwd pinned to the session workspace root" in item for item in prompt["repair_contract"]["pytest_repair_requirements"])
    assert "pytest temporary directory" in prompt["instruction"]


def test_pytest_failures_parses_inline_and_structured_formats() -> None:
    inline_output = (
        "generated/tests/test_agent.py::test_required_columns FAILED [ 25%]\n"
        "generated/tests/test_agent.py::test_row_count FAILED\n"
    )
    assert _pytest_failures(inline_output) == [
        "generated/tests/test_agent.py::test_required_columns",
        "generated/tests/test_agent.py::test_row_count",
    ]
    assert _pytest_failures(
        "no pytest lines here",
        failing_tests=["generated/tests/test_agent.py::test_required_columns"],
    ) == ["generated/tests/test_agent.py::test_required_columns"]


def test_pytest_assertion_lines_parses_e_prefix_and_assertion_error() -> None:
    output = (
        "E   assert ('' is not None and '' != '')\n"
        "AssertionError: assert ('' is not None and '' != '')\n"
    )
    assertions = _pytest_assertion_lines(output)
    assert assertions[0] == "assert ('' is not None and '' != '')"
    assert assertions[1] == "AssertionError: assert ('' is not None and '' != '')"


def test_pytest_failure_requires_agent_repair_when_required_columns_empty(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    outputs = workspace / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "output.csv").write_text(
        "txn_id,category,rule_matched,rule_used,confidence\n"
        "T-1,Office Expense,,,0.85\n",
        encoding="utf-8",
    )
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "category", "rule_matched", "rule_used", "confidence"],
            "required_output_columns": [
                "category",
                "rule_matched",
                "rule_used",
                "confidence",
            ],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    pytest_output = (
        "generated/tests/test_agent.py::test_required_columns FAILED\n"
        "AssertionError: assert ('' is not None and '' != '')\n"
    )
    assert _pytest_failure_requires_agent_repair(
        workspace=workspace,
        contract=contract,
        pytest_output=pytest_output,
    )


def test_pytest_failure_does_not_require_agent_repair_for_import_only_failure(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "category"],
            "required_output_columns": ["category"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    pytest_output = "NameError: name 'sys' is not defined\n"
    assert not _pytest_failure_requires_agent_repair(
        workspace=workspace,
        contract=contract,
        pytest_output=pytest_output,
    )


def test_pytest_signals_classification_agent_failure_for_missing_report_category() -> None:
    assertions = ["AssertionError: Category 'Bank Fees' missing from report"]
    assert _pytest_signals_classification_agent_failure(assertions) is True


def test_pytest_signals_clear_rule_confidence_test_misinterpretation() -> None:
    assertions = [
        "AssertionError: Row 6: rule_matched='No rule matched' but confidence 0.4 < 0.80"
    ]
    assert _pytest_signals_clear_rule_confidence_test_misinterpretation(assertions) is True


def test_pytest_failure_repair_kind_routes_classification_failures_to_agent_repair(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "model_authored_finance_workflow",
            "build_mode": "llm_custom",
            "input_file": "uploads/bank_transaction_categorisation_demo.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["transaction_id"],
            "output_columns": ["transaction_id", "category"],
            "required_output_columns": ["category"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    pytest_output = (
        "generated/tests/test_agent.py::test_report_contains_summary_evidence FAILED\n"
        "AssertionError: Category 'Bank Fees' missing from report\n"
        "generated/tests/test_agent.py::test_confidence_score_constraints FAILED\n"
        "AssertionError: Row 6: rule_matched='No rule matched' but confidence 0.4 < 0.80\n"
    )
    assert _pytest_failure_repair_kind(
        workspace=workspace,
        contract=contract,
        pytest_output=pytest_output,
    ) == "contract_validation"


def test_pytest_failure_repair_kind_keeps_test_only_clear_rule_misinterpretation(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "demo",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "category"],
            "required_output_columns": ["category"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    pytest_output = (
        "generated/tests/test_agent.py::test_confidence_score_constraints FAILED\n"
        "AssertionError: Row 2: rule_matched='No rule matched' but confidence 0.4 < 0.80\n"
    )
    assert _pytest_failure_repair_kind(
        workspace=workspace,
        contract=contract,
        pytest_output=pytest_output,
    ) == "pytest"


def test_pytest_signals_expense_brittle_test_failure() -> None:
    fixture = json.loads(_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN.read_text(encoding="utf-8"))
    assertions = fixture["assertion_failures"]
    assert _pytest_signals_expense_brittle_test_failure(assertions) is True


def test_pytest_signals_expense_brittle_semicolon_rule_used_and_synth_path() -> None:
    assertions = [
        (
            "AssertionError: Rule name 'amount_over_limit; missing_receipt' "
            "not in permitted names: {'amount_over_limit'}"
        ),
        "FileNotFoundError: outputs/synth_exceptions.csv",
    ]
    assert _pytest_signals_expense_brittle_test_failure(assertions) is True


def test_pytest_failure_repair_kind_routes_expense_brittle_failures_to_test_repair(
    workspaces_root: Path,
) -> None:
    fixture = json.loads(_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN.read_text(encoding="utf-8"))
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
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
            "output_columns": ["expense_id", "exception_flag", "rule_used"],
            "required_output_columns": ["exception_flag", "rule_used"],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    pytest_output = (
        f"generated/tests/test_agent.py::{fixture['failing_tests'][0]} FAILED\n"
        f"{fixture['assertion_failures'][0]}\n"
        f"generated/tests/test_agent.py::{fixture['failing_tests'][1]} FAILED\n"
        f"{fixture['assertion_failures'][1]}\n"
    )
    assert _pytest_failure_repair_kind(
        workspace=workspace,
        contract=contract,
        pytest_output=pytest_output,
    ) == "pytest"


def test_expense_pytest_after_golden_fixture_matches_session_assertions() -> None:
    fixture = json.loads(_FIXTURE_9980BF32_PYTEST_AFTER_GOLDEN.read_text(encoding="utf-8"))
    assert fixture["golden_rows_matched"] == "7/7"
    assert "personal weekend travel" in fixture["assertion_failures"][1]
    assert fixture["contract_allowed_enums_keys"] == [
        "exception_flag",
        "review_required",
        "severity",
    ]


def test_bank_generated_pytest_failure_fixture_matches_session_assertions() -> None:
    fixture = json.loads(_FIXTURE_BANK_GENERATED_PYTEST_FAILURE.read_text(encoding="utf-8"))
    assert fixture["session_id"] == "5aa804ba-0272-4c65-84fd-cfc94c56bfdc"
    assert fixture["root_cause_classification"] == 7
    assert "Category 'Bank Fees' missing from report" in fixture["assertion_failures"][0]


def test_contract_validation_failure_preserves_technical_detail_when_repair_fails(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    base_script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    base_script[2] = _replace_agent_source(base_script[2], _validation_null_agent_source())
    base_script[3] = _replace_test_source(base_script[3], _pytest_contract_backed_test_source())
    base_script.append(
        _response(
            "contract-validation-illegal-test-rewrite",
            {
                "files": [
                    {
                        "path": "generated/tests/test_agent.py",
                        "content": "def test_wrong_scope():\n    assert False\n",
                    }
                ],
                "notes": "Wrongly edits tests during contract validation repair.",
                "assumptions": [],
            },
        )
    )

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=FakeModelClient(script=base_script),
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.CONTRACT_SPECIFIC_VALIDATION_FAILED
    failed = next(
        event for event in event_log.read_all(sid) if event.kind == EventKind.WORKFLOW_FAILED
    )
    detail = failed.payload["technical_detail"]
    assert "Original contract validation failure before repair" in detail
    assert "required_column_null_counts={'rule_matched': 200, 'rule_used': 200}" in detail
    assert "\"_missing_required_columns\": \"rule_matched,rule_used\"" in detail
    assert "Latest contract validation repair failure" in detail
    assert "unexpected_paths=generated/tests/test_agent.py" in detail


def test_optional_exception_output_file_absent_does_not_fail_or_create_fallback(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
        contract_overrides={
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "validation_report",
                    "description": "Validation report explaining rules and counts",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                    "source": "platform_canonical",
                },
            ],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["txn_id", "issue_flag", "issue_reason"],
                    "optional_columns": [],
                }
            ],
            "exception_rules": [
                {
                    "name": "low_confidence",
                    "condition": "confidence < 0.8",
                    "output_column": "issue_flag",
                    "reason": "Low confidence in category assignment",
                    "severity": "high",
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
                ],
                "issue_flag": ["no_issue", "review_required"],
            },
        },
    )
    agent_without_exceptions = _agent_source_from(script[2]).replace(
        "    _write_exceptions(output_rows, contract, output_columns)\n",
        "",
    )
    script[2] = _replace_agent_source(script[2], agent_without_exceptions)
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert (workspace / "outputs" / "output.csv").is_file()
    assert (workspace / "reports" / "validation_report.md").is_file()
    assert not (workspace / "outputs" / "exceptions.csv").exists()

    events = event_log.read_all(sid)
    validation = next(
        event.payload
        for event in events
        if event.kind == EventKind.VALIDATION_RUN
    )
    exception_check = next(
        check for check in validation["layer_results"] if check["name"] == "Exception list consistency"
    )
    assert exception_check["skipped"] is True
    assert exception_check["passed"] is None
    assert exception_check["evidence"] == "optional exception file not produced: exceptions.csv"
    assert not any(
        event.kind == EventKind.MODEL_CALLED
        and event.payload.get("purpose") == "execution_repair"
        for event in events
    )


def test_pytest_repair_candidate_must_pass_candidate_pytest_before_promotion(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    repair_attempt_1 = _response(
        "pytest-repair-missing-import",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_missing_import_repair_source(),
                }
            ],
            "notes": "Fix the row count assertion.",
            "assumptions": [],
        },
    )
    repair_attempt_2 = _response(
        "pytest-repair-valid",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_contract_backed_test_source(),
                }
            ],
            "notes": "Return a self-contained valid pytest file.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_test_source(_pytest_row_count_mismatch_test_source())
    script.extend([repair_attempt_1, repair_attempt_2])
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert (
        workspace / "generated" / "repairs" / "attempt_1" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_missing_import_repair_source()
    assert (
        workspace / "generated" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_contract_backed_test_source()

    events = event_log.read_all(sid)
    candidate_runs = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "pytest_candidate_run"
    ]
    assert [(payload["attempt"], payload["failed"]) for payload in candidate_runs] == [
        (1, 2),
        (2, 0),
    ]
    promoted = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "repair_candidate_promoted"
    ]
    assert [payload["attempt"] for payload in promoted] == [2]
    rejected = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "repair_candidate_rejected"
        and event.payload.get("failure_kind") == "pytest"
    ]
    assert [payload["attempt"] for payload in rejected] == [1]

    prompt_2 = json.loads(model.calls[5]["messages"][0]["content"][0]["text"])
    current_test = next(
        item for item in prompt_2["current_files"] if item["path"] == "generated/tests/test_agent.py"
    )
    assert current_test["content"] == _pytest_row_count_mismatch_test_source()
    assert "NameError: name 'subprocess' is not defined" in prompt_2["failure_detail"]


def test_pytest_repair_invalid_candidates_fail_closed_and_preserve_original_failure(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    bad_repair = _response(
        "pytest-repair-still-missing-import",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_missing_import_repair_source(),
                }
            ],
            "notes": "Still broken.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_test_source(_pytest_row_count_mismatch_test_source())
    script.extend([bad_repair, bad_repair])
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.GENERATED_PYTEST_FAILED
    assert (
        workspace / "generated" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_row_count_mismatch_test_source()
    assert (
        workspace / "generated" / "repairs" / "attempt_2" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_missing_import_repair_source()

    prompt_2 = json.loads(model.calls[5]["messages"][0]["content"][0]["text"])
    current_test = next(
        item for item in prompt_2["current_files"] if item["path"] == "generated/tests/test_agent.py"
    )
    assert current_test["content"] == _pytest_row_count_mismatch_test_source()
    assert "NameError: name 'subprocess' is not defined" in prompt_2["failure_detail"]

    events = event_log.read_all(sid)
    assert not any(
        event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "repair_candidate_promoted"
        and event.payload.get("failure_kind") == "pytest"
        for event in events
    )
    failed = next(event for event in events if event.kind == EventKind.WORKFLOW_FAILED)
    assert "AssertionError: assert 201 == 200" in failed.payload["technical_detail"]
    assert "NameError: name 'subprocess' is not defined" in failed.payload["technical_detail"]


def test_pytest_repair_rejects_invented_business_rule_candidate_before_promotion(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    repair_attempt_1 = _response(
        "pytest-repair-invented-sign-rule",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_invented_sign_rule_test_source(),
                }
            ],
            "notes": "Adds a guessed sign-based business rule test.",
            "assumptions": [],
        },
    )
    repair_attempt_2 = _response(
        "pytest-repair-valid",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_contract_backed_test_source(),
                }
            ],
            "notes": "Return a contract-backed pytest file.",
            "assumptions": [],
        },
    )
    script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    script[2] = _replace_agent_source(script[2], _session_like_positive_office_expense_agent_source())
    script[3] = _replace_test_source(script[3], _pytest_header_blind_test_source())
    script.extend([repair_attempt_1, repair_attempt_2])
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert (
        workspace / "generated" / "repairs" / "attempt_1" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_invented_sign_rule_test_source()
    assert (
        workspace / "generated" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_contract_backed_test_source()

    events = event_log.read_all(sid)
    candidate_runs = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "pytest_candidate_run"
    ]
    assert [(payload["attempt"], payload["failed"]) for payload in candidate_runs] == [
        (1, 1),
        (2, 0),
    ]
    promoted = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "repair_candidate_promoted"
        and event.payload.get("failure_kind") == "pytest"
    ]
    assert [payload["attempt"] for payload in promoted] == [2]
    rejected = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "repair_candidate_rejected"
        and event.payload.get("failure_kind") == "pytest"
    ]
    assert [payload["attempt"] for payload in rejected] == [1]
    assert "assert 45.98 < 0" in rejected[0]["output_excerpt"]

    prompt_2 = json.loads(model.calls[5]["messages"][0]["content"][0]["text"])
    current_test = next(
        item for item in prompt_2["current_files"] if item["path"] == "generated/tests/test_agent.py"
    )
    assert current_test["content"] == _pytest_header_blind_test_source()
    assert "assert 45.98 < 0" in prompt_2["failure_detail"]


def test_pytest_repair_replaces_raw_summary_metric_identifier_assertions_with_semantic_report_checks(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    repair_attempt = _response(
        "pytest-repair-semantic-report-headings",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_semantic_report_heading_test_source(),
                }
            ],
            "notes": "Replace raw summary metric identifiers with semantic report checks.",
            "assumptions": [],
        },
    )
    script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    script[2] = _replace_agent_source(script[2], _session_like_positive_office_expense_agent_source())
    script[3] = _replace_test_source(script[3], _pytest_report_metric_identifier_literalism_source())
    script.append(repair_attempt)
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert (
        workspace / "generated" / "tests" / "test_agent.py"
    ).read_text(encoding="utf-8") == _pytest_semantic_report_heading_test_source()

    prompt = json.loads(model.calls[4]["messages"][0]["content"][0]["text"])
    assert prompt["failure_kind"] == "pytest"
    assert "category_counts" in prompt["failure_detail"]
    assert "uncertain_rows_count" in prompt["failure_detail"]
    assert (
        "internal identifiers unless the contract explicitly requires exact literal text"
        in " ".join(prompt["repair_contract"]["pytest_repair_requirements"])
    )
    assert (
        "normalized human-readable headings or data-derived evidence checks"
        in " ".join(prompt["repair_contract"]["pytest_repair_requirements"])
    )
    assert "raw snake_case metric identifier" in prompt["instruction"]

    events = event_log.read_all(sid)
    candidate_runs = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "pytest_candidate_run"
    ]
    assert [(payload["attempt"], payload["failed"]) for payload in candidate_runs] == [(1, 0)]
    promoted = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "repair_candidate_promoted"
        and event.payload.get("failure_kind") == "pytest"
    ]
    assert [payload["attempt"] for payload in promoted] == [1]


def test_runtime_repair_escalates_to_planning_model_after_invalid_candidate(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    invalid_candidate = _syntax_broken_agent_source()
    repair_response = _response(
        "execution-repair-invalid-syntax",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": invalid_candidate,
                }
            ],
            "notes": "Broken syntax.",
            "assumptions": [],
        },
    )
    script = iter(
        _direct_bank_author_script_with_agent_source(_name_error_agent_source())
        + [repair_response, repair_response]
    )
    models_seen: list[str] = []

    async def _fake_complete(self, *, system_prompt, messages, tools, max_tokens=4096):
        del system_prompt, messages, tools, max_tokens
        models_seen.append(self.model)
        return next(script)

    monkeypatch.setattr(OllamaModelClient, "complete", _fake_complete)
    settings = Settings(
        llm_provider="ollama",
        ollama_planning_model="qwen2.5-coder:14b",
        ollama_codegen_model="qwen2.5-coder:7b",
        author_max_repair_attempts=2,
    )

    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=OllamaModelClient(model="qwen2.5-coder:14b"),
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    assert models_seen[:6] == [
        "qwen2.5-coder:14b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:14b",
    ]
    repair_calls = [
        event.payload
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
        and event.payload.get("purpose") == "execution_repair"
    ]
    assert [(payload["attempt"], payload["model"]) for payload in repair_calls] == [
        (1, "qwen2.5-coder:7b"),
        (2, "qwen2.5-coder:14b"),
    ]


def test_pytest_repair_uses_planning_model_on_first_attempt(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    repair_response = _response(
        "pytest-repair-valid",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": _pytest_contract_backed_test_source(),
                }
            ],
            "notes": "Return a self-contained valid pytest file.",
            "assumptions": [],
        },
    )
    script = iter(
        _direct_bank_author_script_with_test_source(_pytest_row_count_mismatch_test_source())
        + [repair_response]
    )
    models_seen: list[str] = []

    async def _fake_complete(self, *, system_prompt, messages, tools, max_tokens=4096):
        del system_prompt, messages, tools, max_tokens
        models_seen.append(self.model)
        return next(script)

    monkeypatch.setattr(OllamaModelClient, "complete", _fake_complete)
    settings = Settings(
        llm_provider="ollama",
        ollama_planning_model="qwen2.5-coder:14b",
        ollama_codegen_model="qwen2.5-coder:7b",
        author_max_repair_attempts=2,
    )

    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=OllamaModelClient(model="qwen2.5-coder:14b"),
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert models_seen[:5] == [
        "qwen2.5-coder:14b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:14b",
    ]
    repair_calls = [
        event.payload
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
        and event.payload.get("purpose") == "execution_repair"
    ]
    assert [(payload["attempt"], payload["model"]) for payload in repair_calls] == [
        (1, "qwen2.5-coder:14b"),
    ]


def test_generated_agent_syntax_preflight_repairs_before_execution(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    base_script = model_authoring_responses(
        template_root=_REPO_ROOT / "templates" / "bank_categoriser",
        workflow_type="bank_transaction_categorisation",
    )
    repaired_source = _agent_source_from(base_script[2])
    repair_response = _response(
        "syntax-repair-valid-agent",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": repaired_source,
                }
            ],
            "notes": "Replace syntactically invalid generated agent.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_agent_source(_syntax_broken_agent_source())
    script.append(repair_response)
    model = FakeModelClient(script=script)

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=model,
        )
    )

    assert status == SessionStatus.COMPLETED
    assert error is None
    assert (workspace / "generated" / "agent.py").read_text(encoding="utf-8") == repaired_source
    assert (workspace / "generated" / "repairs" / "attempt_1" / "agent.py").is_file()
    assert (workspace / "generated" / "tests" / "test_agent.py").is_file()
    assert not (workspace / "test_agent.py").exists()
    assert (workspace / "outputs" / "output.csv").is_file()
    assert (workspace / "reports" / "validation_report.md").is_file()

    events = event_log.read_all(sid)
    preflights = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "custom_workflow_syntax_preflight"
    ]
    assert preflights[0]["status"] == "failed"
    assert preflights[0]["error_type"] == "SyntaxError"
    assert preflights[0]["path"] == "generated/agent.py"
    assert preflights[0]["line_number"] == _syntax_broken_line_number()
    assert "with open(args.input" in preflights[0]["failing_line"]
    assert "nearby_code_excerpt" in preflights[0]
    assert any(p["status"] == "passed" and p["repair_attempt"] == 1 for p in preflights)

    executions = [
        event.payload
        for event in events
        if event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "custom_workflow_execution"
    ]
    assert executions
    assert executions[0]["repair_attempt"] == 1
    assert executions[0]["exit_code"] == 0
    assert "--contract generated/author_output_contract.json" in executions[0]["command"]
    assert "--row-output outputs/output.csv" in executions[0]["command"]
    assert "--report-path reports/validation_report.md" in executions[0]["command"]

    repair_prompt = model.calls[4]["messages"][0]["content"][0]["text"]
    assert "SyntaxError" in repair_prompt
    assert "filename=generated/agent.py" in repair_prompt
    assert f"line_number={_syntax_broken_line_number()}" in repair_prompt
    assert "failing_line=    with open(args.input" in repair_prompt
    assert "nearby_code_excerpt" in repair_prompt
    repair_payload = json.loads(repair_prompt)
    current_agent = next(
        item for item in repair_payload["current_files"] if item["path"] == "generated/agent.py"
    )
    assert current_agent["content"] == _syntax_broken_agent_source()


def test_generated_code_repair_exhaustion_fails_honestly(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    bad_repair = _response(
        "execution-repair-still-broken",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": _bad_agent_source(),
                }
            ],
            "notes": "Still broken.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_agent_source(_bad_agent_source())
    script.extend([bad_repair, bad_repair])

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=FakeModelClient(script=script),
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    purposes = [
        event.payload.get("purpose")
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    ]
    assert purposes.count("execution_repair") == 2
    assert not any(event.kind == EventKind.WORKFLOW_COMPLETED for event in event_log.read_all(sid))


def test_generated_agent_invalid_syntax_repair_fails_closed_before_execution(
    workspaces_root: Path,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    upload = workspace / "uploads" / "sample_input.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "bank_categoriser" / "data" / "sample_input.csv", upload)
    event_log = EventLog(wm)
    bad_repair = _response(
        "syntax-repair-still-invalid",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": _syntax_broken_agent_source(),
                }
            ],
            "notes": "Still invalid.",
            "assumptions": [],
        },
    )
    script = _direct_bank_author_script_with_agent_source(_syntax_broken_agent_source())
    script.extend([bad_repair, bad_repair])

    settings = get_settings().model_copy(update={"author_max_repair_attempts": 2})
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=sid,
            workspace=workspace,
            upload=UploadedDataFile(path=upload, format="csv"),
            workflow_type="bank_transaction_categorisation",
            user_description="Categorise these bank transactions.",
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=0,
            model_client=FakeModelClient(script=script),
        )
    )

    assert status == SessionStatus.FAILED_OTHER
    assert error == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    events = event_log.read_all(sid)
    assert not any(
        event.kind == EventKind.DECISION_INPUT
        and event.payload.get("kind") == "custom_workflow_execution"
        for event in events
    )
    assert not (workspace / "outputs" / "output.csv").exists()
    assert not (workspace / "reports" / "validation_report.md").exists()
    failed = next(event for event in events if event.kind == EventKind.WORKFLOW_FAILED)
    assert failed.payload["error_code"] == ErrorCode.AUTHOR_GENERATED_CODE_FAILED.value
    assert "Repair candidate failed Python syntax preflight before promotion." in failed.payload["technical_detail"]
    assert "SyntaxError" in failed.payload["technical_detail"]
    purposes = [
        event.payload.get("purpose")
        for event in events
        if event.kind == EventKind.MODEL_CALLED
    ]
    assert purposes.count("execution_repair") == 2


async def _run_author_live_processor_export_xlsx(
    workspaces_root: Path,
    e2e_db,
    *,
    rows: int = 80,
):
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    settings = get_settings()
    upload_path = workspace / "uploads" / "payment_processor_reconciliation_sample.xlsx"
    _write_processor_export_xlsx(upload_path, rows=rows)
    event_log = EventLog(wm)
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={"kind": "author_user_workflow", "text": _LIVE_EXPORT_PROMPT},
        step=0,
    )
    event_log.append(
        session_id=sid,
        kind=EventKind.FILE_UPLOADED,
        actor_type=ActorType.USER,
        payload={
            "filename": upload_path.name,
            "relative_path": str(upload_path.relative_to(workspace)),
            "size_bytes": upload_path.stat().st_size,
        },
        step=0,
    )
    loop = AgentLoop(
        registry=build_registry(),
        model_client=FakeModelClient(script=_payment_model_responses()),
        idempotency_store=IdempotencyStore(db=e2e_db),
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    flow = AuthorFlow(
        agent_loop=loop,
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    outcome = await flow.run(
        session_id=sid,
        system_prompt="author",
        initial_messages=[],
        budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=120),
    )
    return wm, event_log, sid, workspace, outcome


def test_live_processor_export_with_mixed_statuses_completes(
    workspaces_root: Path,
    e2e_db,
) -> None:
    async def _run() -> None:
        wm = WorkspaceManager(root=workspaces_root)
        sid = uuid4()
        wm.allocate(sid, Workflow.AUTHOR)
        workspace = wm.get(sid)
        upload_path = workspace / "uploads" / "payment_processor_reconciliation_sample.xlsx"
        _write_processor_export_xlsx(upload_path, rows=80, mixed_edge_cases=True)
        event_log = EventLog(wm)
        event_log.append(
            session_id=sid,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.USER,
            payload={"kind": "author_user_workflow", "text": _LIVE_EXPORT_PROMPT},
            step=0,
        )
        event_log.append(
            session_id=sid,
            kind=EventKind.FILE_UPLOADED,
            actor_type=ActorType.USER,
            payload={
                "filename": upload_path.name,
                "relative_path": str(upload_path.relative_to(workspace)),
                "size_bytes": upload_path.stat().st_size,
            },
            step=0,
        )
        settings = get_settings()
        flow = AuthorFlow(
            agent_loop=AgentLoop(
                registry=build_registry(),
                model_client=FakeModelClient(script=_payment_model_responses()),
                idempotency_store=IdempotencyStore(db=e2e_db),
                event_log=event_log,
                workspace_manager=wm,
                settings=get_settings(),
            ),
            event_log=event_log,
            workspace_manager=wm,
            settings=get_settings(),
        )
        outcome = await flow.run(
            session_id=sid,
            system_prompt="author",
            initial_messages=[],
            budgets=LoopBudgets(max_steps=10, max_tokens=200_000, max_wall_seconds=120),
        )
        assert outcome.terminal_status == SessionStatus.COMPLETED
        assert outcome.terminal_error_code is None
        failed = next(
            (e for e in event_log.read_all(sid) if e.kind == EventKind.WORKFLOW_FAILED),
            None,
        )
        assert failed is None

    asyncio.run(_run())


def test_live_processor_export_xlsx_completes_with_eighty_rows(
    workspaces_root: Path,
    e2e_db,
) -> None:
    async def _run() -> None:
        _, event_log, sid, workspace, outcome = await _run_author_live_processor_export_xlsx(
            workspaces_root,
            e2e_db,
            rows=80,
        )
        assert outcome.terminal_status == SessionStatus.COMPLETED
        assert outcome.terminal_error_code is None
        assert (workspace / "generated" / "agent.py").is_file()
        assert (workspace / "generated" / "tests").is_dir()
        assert (workspace / "outputs" / "output.csv").is_file()
        assert (workspace / "outputs" / "summary_by_settlement_batch.csv").is_file()
        assert (workspace / "reports" / "validation_report.md").is_file()
        assert (workspace / "SESSION_README.md").is_file()
        assert (workspace / "archive.zip").is_file()

        with (workspace / "uploads" / "normalised_input.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            input_rows = sum(1 for _ in handle) - 1
        with (workspace / "outputs" / "output.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            output_rows = sum(1 for _ in handle) - 1
        assert input_rows == 80
        assert output_rows == 80

        events = event_log.read_all(sid)
        assert not any(e.kind == EventKind.TEMPLATE_SEEDED for e in events)
        validation = next(e for e in events if e.kind == EventKind.VALIDATION_RUN)
        assert validation.payload["overall_passed"] is True
        completed = next(e for e in events if e.kind == EventKind.WORKFLOW_COMPLETED)
        assert completed.payload["via"] == AI_AUTHORED_WORKFLOW_BUILD_VIA

        manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
        completion = manifest.get("completion") or {}
        assert completion.get("selected_sheet") == "processor_export"
        assert "golden_output" in (completion.get("skipped_layers") or [])

    asyncio.run(_run())


def test_assess_routes_unknown_finance_tabular_upload_to_llm_authoring() -> None:
    gate, assessment = assess_author_pre_pipeline(
        user_description="Prepare GL journal entries from this export.",
        template_name=None,
        columns=["account_code", "memo"],
        upload_format="csv",
    )
    assert gate == "custom_build"
    assert assessment is None


def test_incompatible_prompt_file_stops_honestly_without_output(
    workspaces_root: Path,
    e2e_db,
) -> None:
    async def _run() -> None:
        wm = WorkspaceManager(root=workspaces_root)
        sid = uuid4()
        wm.allocate(sid, Workflow.AUTHOR)
        workspace = wm.get(sid)
        csv_path = workspace / "uploads" / "gl_codes.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("account_code,memo\n1000,Opening\n", encoding="utf-8")
        event_log = EventLog(wm)
        event_log.append(
            session_id=sid,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.USER,
            payload={
                "kind": "author_user_workflow",
                "text": "Prepare GL journal entries from this export.",
            },
            step=0,
        )
        event_log.append(
            session_id=sid,
            kind=EventKind.FILE_UPLOADED,
            actor_type=ActorType.USER,
            payload={
                "filename": "gl_codes.csv",
                "relative_path": "uploads/gl_codes.csv",
                "size_bytes": 32,
            },
            step=0,
        )
        settings = get_settings()
        loop = AgentLoop(
            registry=build_registry(),
            model_client=FakeModelClient(script=model_ack_only_responses(1)),
            idempotency_store=IdempotencyStore(db=e2e_db),
            event_log=event_log,
            workspace_manager=wm,
            settings=get_settings(),
        )
        flow = AuthorFlow(
            agent_loop=loop,
            event_log=event_log,
            workspace_manager=wm,
            settings=get_settings(),
        )
        outcome = await flow.run(
            session_id=sid,
            system_prompt="author",
            initial_messages=[],
            budgets=LoopBudgets(max_steps=5, max_tokens=50_000, max_wall_seconds=60),
        )
        assert outcome.terminal_status == SessionStatus.FAILED_OTHER
        assert outcome.terminal_error_code is not None
        assert outcome.terminal_error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
        assert not (workspace / "outputs" / "output.csv").exists()
        events = event_log.read_all(sid)
        assert any(e.kind == EventKind.MODEL_CALLED for e in events)
        assert not any(e.kind == EventKind.WORKFLOW_COMPLETED for e in events)

    asyncio.run(_run())


def test_completion_failure_metadata_nested_in_manifest(tmp_path: Path) -> None:
    from agentforge.schemas import CompletionFailureMetadata, CompletionMetadata

    completion = CompletionMetadata(
        workflow_type="payment_processor_reconciliation",
        build_mode="llm_custom",
        completion_via="ai_authored_workflow_build",
        validation_overall="fail",
        failure=CompletionFailureMetadata(
            error_code="author_validation_failed",
            failed_layer="Row-level invariants",
            failed_check="Row-level validation failed because the selected row key was not unique.",
            validation_failures=["Row-level invariants (row_level): primary key 'payout_id' not unique"],
            pytest_summary="7 passed, 0 failed",
            pytest_passed=7,
            pytest_failed=0,
        ),
    )
    payload = completion.model_dump(mode="json")
    assert payload["failure"]["error_code"] == "author_validation_failed"
    assert payload["failure"]["failed_layer"] == "Row-level invariants"
    assert "failed_check" not in payload
    assert "pytest_summary" not in payload


def test_failure_manifest_records_workflow_and_system_report_paths(tmp_path: Path) -> None:
    import zipfile

    from agentforge.orchestrator.author_custom_build import (
        _finalize_custom_workflow_failure,
        _publish_custom_workflow_validation,
    )
    from tests.test_author_artifact_report_separation import (
        _minimal_contract,
        _sample_report,
    )

    session_id = uuid4()
    wm = WorkspaceManager(root=tmp_path)
    wm.allocate(session_id, Workflow.AUTHOR)
    workspace = wm.get(session_id)
    event_log = EventLog(wm)
    contract = _minimal_contract()
    output_csv = workspace / "outputs" / "output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_csv.write_text(
        "transaction_id,category,confidence\n1,Travel,1.0\n",
        encoding="utf-8",
    )
    workflow_report = workspace / "reports" / "validation_report.md"
    workflow_report.parent.mkdir(parents=True, exist_ok=True)
    workflow_report.write_text("# Workflow report\n", encoding="utf-8")
    report = _sample_report(session_id)

    ingest = type(
        "Ingest",
        (),
        {
            "original_upload_path": "uploads/input.csv",
            "upload_format": "csv",
            "selected_sheet": None,
            "normalized_input_path": "uploads/input.csv",
        },
    )()

    _publish_custom_workflow_validation(
        session_id=session_id,
        workspace=workspace,
        ingest=ingest,
        contract=contract,
        report=report,
        output_abs=output_csv,
        event_log=event_log,
        step=1,
        stage_label="validate",
        workflow_type=contract.workflow_type,
    )

    _finalize_custom_workflow_failure(
        session_id=session_id,
        workspace=workspace,
        workspace_manager=wm,
        ingest=ingest,
        contract=contract,
        report=report,
        workflow_type=contract.workflow_type,
        step=2,
        stage_label="validate",
        event_log=event_log,
        error_code=ErrorCode.AUTHOR_VALIDATION_FAILED,
        message="failed",
        failed_check="Broken validation",
        failed_layer="row_level",
    )

    manifest = wm.read_manifest(session_id)
    assert manifest.status == SessionStatus.FAILED_OTHER
    assert manifest.completion is not None
    assert manifest.completion.validation_report_path == "reports/system_validation_report.md"
    assert manifest.completion.workflow_report_path == "reports/validation_report.md"

    archive_path = workspace / "archive.zip"
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
    assert "reports/system_validation_report.md" in names
    assert "reports/validation_report.md" in names
