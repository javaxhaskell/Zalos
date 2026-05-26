"""Author completion gate — model contribution required for successful Author sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from agentforge.models import FakeModelClient, ModelResponse, TextBlock
from agentforge.orchestrator.author_llm_authoring import (
    apply_expense_exception_golden_policy,
    effective_author_model_stages,
    enforce_author_completion_gate,
    run_model_authoring_pipeline,
    sync_author_output_contract_provenance,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, ErrorCode, EventKind, Workflow
from agentforge.schemas.author_output_contract import AuthorOutputContract
from tests.author_model_fixtures import (
    model_ack_only_responses,
    model_authoring_responses,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"
_FIXTURE_22C0C771_EVAL_SAFETY_FAILURE = (
    Path(__file__).resolve().parent
    / "fixtures/generated_agent_candidates/vendor_payment_eval_safety_failure_22c0c771.json"
)


def _response(id_: str, payload) -> ModelResponse:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return ModelResponse(
        id=id_,
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
    )


def _base_script() -> list[ModelResponse]:
    return model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )


def _payload(response: ModelResponse) -> dict:
    return json.loads(response.text)


def _file_content(response: ModelResponse, rel_path: str) -> str:
    payload = _payload(response)
    for item in payload["files"]:
        if item["path"] == rel_path:
            return item["content"]
    raise AssertionError(f"{rel_path} missing from scripted response")


def _purposes(event_log: EventLog, sid) -> list[str]:
    return [
        str(event.payload.get("purpose"))
        for event in event_log.read_all(sid)
        if event.kind == EventKind.MODEL_CALLED
    ]


def _run_pipeline(workspaces_root, script: list[ModelResponse]):
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
            stage_label="test_gate",
            model_client=FakeModelClient(script=script),
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
        )
    )
    return result, event_log, sid, workspace


def test_completion_gate_rejects_zero_model_calls(workspaces_root) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    event_log = EventLog(wm)
    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=1,
        stage_label="test_gate",
    )
    assert error == ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING
    failed = [
        event
        for event in event_log.read_all(sid)
        if event.kind == EventKind.WORKFLOW_FAILED
    ]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING.value


def test_completion_gate_rejects_model_calls_without_contributed_files(
    workspaces_root,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    model = FakeModelClient(script=model_ack_only_responses())
    result = asyncio.run(
        run_model_authoring_pipeline(
            session_id=sid,
            event_log=event_log,
            step=1,
            stage_label="test_gate",
            model_client=model,
            workspace=workspace,
            user_description="Categorise bank transactions.",
            schema_profile={"columns": ["txn_id", "amount"]},
            reference_scaffold_root=None,
            workflow_type="bank_transaction_categorisation",
        )
    )
    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_gate",
        workspace=workspace,
    )
    assert error == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    failed = [
        event
        for event in event_log.read_all(sid)
        if event.kind == EventKind.WORKFLOW_FAILED
    ]
    assert failed
    assert failed[-1].payload["error_code"] == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED.value
    assert not any(event.kind == EventKind.WORKFLOW_COMPLETED for event in event_log.read_all(sid))


def test_completion_gate_rejects_missing_contract_review_stage(workspaces_root) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)
    (workspace / "generated").mkdir(parents=True, exist_ok=True)
    (workspace / "generated" / "model_contract_plan.json").write_text(
        json.dumps(_payload(_base_script()[0])["author_output_contract"]),
        encoding="utf-8",
    )
    event_log.append(
        session_id=sid,
        kind=EventKind.MODEL_CALLED,
        actor_type=ActorType.MODEL,
        payload={
            "stage": "test_gate",
            "purpose": "contract_planning",
            "usage": {"total_tokens": 10},
        },
        step=1,
    )

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_gate",
        workspace=workspace,
    )

    assert error == ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED


def test_effective_author_model_stages_includes_reference_scaffold_stages_when_scaffold_mode(
    workspaces_root,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    event_log = EventLog(wm)
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "reference_sample_scaffold_mode",
            "stage": "author_custom_build",
            "template": "bank_categoriser",
        },
        step=1,
    )
    for stage in ("contract_planning", "contract_review"):
        event_log.append(
            session_id=sid,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "reference_sample_contract_scaffold_used",
                "stage": stage,
                "template": "bank_categoriser",
                "mode": "planned_contract_seed",
            },
            step=1,
        )
    for purpose in ("code_generation", "test_generation"):
        event_log.append(
            session_id=sid,
            kind=EventKind.MODEL_CALLED,
            actor_type=ActorType.MODEL,
            payload={"purpose": purpose, "usage": {"total_tokens": 10}},
            step=2,
        )

    stages = effective_author_model_stages(event_log.read_all(sid))
    assert stages == [
        "code_generation",
        "test_generation",
        "contract_planning",
        "contract_review",
    ]


def test_effective_author_model_stages_ignores_scaffold_without_scaffold_mode(
    workspaces_root,
) -> None:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    event_log = EventLog(wm)
    for stage in ("contract_planning", "contract_review"):
        event_log.append(
            session_id=sid,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "reference_sample_contract_scaffold_used",
                "stage": stage,
                "template": "bank_categoriser",
                "mode": "planned_contract_seed",
            },
            step=1,
        )
    for purpose in ("code_generation", "test_generation"):
        event_log.append(
            session_id=sid,
            kind=EventKind.MODEL_CALLED,
            actor_type=ActorType.MODEL,
            payload={"purpose": purpose, "usage": {"total_tokens": 10}},
            step=2,
        )

    stages = effective_author_model_stages(event_log.read_all(sid))
    assert stages == ["code_generation", "test_generation"]


def test_completion_gate_accepts_reference_scaffold_contract_stages(
    workspaces_root,
) -> None:
    script = _base_script()
    result, source_event_log, source_sid, template_workspace = _run_pipeline(
        workspaces_root, script
    )
    assert result.ok is True

    provenance_payload: dict | None = None
    for event in source_event_log.read_all(source_sid):
        if event.kind != EventKind.DECISION_INPUT:
            continue
        if (event.payload or {}).get("kind") == "model_authoring_provenance":
            provenance_payload = event.payload
            break
    assert provenance_payload is not None

    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    workspace = wm.get(sid)
    event_log = EventLog(wm)

    shutil.copytree(
        template_workspace / "generated",
        workspace / "generated",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        template_workspace / "reports",
        workspace / "reports",
        dirs_exist_ok=True,
    )

    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "reference_sample_scaffold_mode",
            "stage": "author_custom_build",
            "template": "bank_categoriser",
        },
        step=1,
    )
    for stage in ("contract_planning", "contract_review"):
        event_log.append(
            session_id=sid,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "reference_sample_contract_scaffold_used",
                "stage": stage,
                "template": "bank_categoriser",
                "mode": "planned_contract_seed",
            },
            step=1,
        )
    for purpose in ("code_generation", "test_generation"):
        event_log.append(
            session_id=sid,
            kind=EventKind.MODEL_CALLED,
            actor_type=ActorType.MODEL,
            payload={"purpose": purpose, "usage": {"total_tokens": 10}},
            step=2,
        )
    event_log.append(
        session_id=sid,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload=provenance_payload,
        step=3,
    )

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=4,
        stage_label="test_gate",
        workspace=workspace,
    )

    assert error is None


def test_malformed_contract_json_triggers_model_json_repair(workspaces_root) -> None:
    script = _base_script()
    malformed = _response("malformed-contract-plan", "{not valid json")
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [malformed, script[0], script[1], script[2], script[3]],
    )

    assert result.ok is True
    assert "json_repair" in _purposes(event_log, sid)
    assert (workspace / "generated" / "model_contract_plan.json").is_file()


def test_json_repair_response_with_inline_comment_is_cleaned(workspaces_root) -> None:
    script = _base_script()
    malformed = _response("malformed-contract-plan", "{not valid json")
    repaired_with_comment = _response(
        "commented-contract-repair",
        script[0].text.replace(
            '"build_mode": "llm_custom",',
            '"build_mode": "llm_custom" // formatting-only note\n,',
            1,
        ),
    )
    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [malformed, repaired_with_comment, script[1], script[2], script[3]],
    )

    assert result.ok is True
    assert "json_repair" in _purposes(event_log, sid)
    saved = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert saved["build_mode"] == "llm_custom"


def test_near_miss_contract_keys_trigger_model_schema_repair_not_mapping(
    workspaces_root,
) -> None:
    script = _base_script()
    invalid_contract = _payload(script[0])["author_output_contract"]
    invalid_contract["calculated_fields"] = [
        {
            "field_name": "category",
            "description": "near-miss key intentionally returned by model",
            "formula": "amount",
        }
    ]
    invalid_plan = _response(
        "near-miss-contract-plan",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )

    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [invalid_plan, script[0], script[1], script[2], script[3]],
    )

    assert result.ok is True
    assert "contract_schema_repair" in _purposes(event_log, sid)
    final_contract = json.loads(
        (workspace / "generated" / "model_contract_plan.json").read_text(encoding="utf-8")
    )
    assert "field_name" not in json.dumps(final_contract)


def test_near_miss_contract_repair_exhaustion_fails_honestly(workspaces_root) -> None:
    script = _base_script()
    invalid_contract = _payload(script[0])["author_output_contract"]
    invalid_contract["calculated_fields"] = [
        {
            "field_name": "category",
            "description": "near-miss key intentionally returned by model",
            "formula": "amount",
        }
    ]
    invalid_plan = _response(
        "near-miss-contract-plan",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )
    still_invalid = _response(
        "near-miss-contract-repair",
        {"author_output_contract": invalid_contract, "planning_notes": []},
    )

    result, event_log, sid, _workspace = _run_pipeline(
        workspaces_root,
        [invalid_plan, still_invalid],
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED
    assert "contract_schema_repair" in _purposes(event_log, sid)


def test_codegen_test_only_legacy_json_fails_without_missing_file_repair(
    workspaces_root,
) -> None:
    script = _base_script()
    generated_test = _file_content(script[3], "generated/tests/test_agent.py")
    code_without_agent = _response(
        "code-generation-without-agent",
        {
            "files": [
                {
                    "path": "generated/tests/test_agent.py",
                    "content": generated_test,
                }
            ],
            "notes": "Missing generated/agent.py by mistake.",
            "assumptions": [],
        },
    )

    result, event_log, _sid, workspace = _run_pipeline(
        workspaces_root,
        [script[0], script[1], code_without_agent],
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_CODE_GENERATION_FAILED
    assert "malformed_agent_source" in (result.technical_detail or "")
    assert "missing_files_repair" not in _purposes(event_log, _sid)
    assert not (workspace / "generated" / "agent.py").exists()


def test_missing_generated_tests_trigger_model_missing_file_repair_failure(
    workspaces_root,
) -> None:
    script = _base_script()
    helper_only_tests = _response(
        "test-generation-without-test-agent",
        {
            "files": [
                {
                    "path": "generated/helper.py",
                    "content": "HELPER = True\n",
                }
            ],
            "notes": "Missing generated/tests/test_agent.py by mistake.",
            "assumptions": [],
        },
    )
    still_missing = _response(
        "missing-test-repair-still-missing",
        {
            "files": [
                {
                    "path": "generated/helper.py",
                    "content": "HELPER = False\n",
                }
            ]
        },
    )

    result, event_log, sid, _workspace = _run_pipeline(
        workspaces_root,
        [script[0], script[1], script[2], helper_only_tests, still_missing],
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_TEST_GENERATION_FAILED
    assert "missing_files_repair" in _purposes(event_log, sid)


def test_unsafe_generated_code_triggers_model_safety_repair(workspaces_root) -> None:
    script = _base_script()
    generated_agent = script[2].text
    unsafe_code = _response(
        "unsafe-code-generation",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": "def main(input_path, contract_path):\n    return eval('1')\n",
                }
            ],
            "notes": "Unsafe code returned by model.",
            "assumptions": [],
        },
    )
    safety_repair = _response(
        "safe-code-repair",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": generated_agent,
                }
            ],
            "notes": "Safe replacement.",
            "assumptions": [],
        },
    )

    result, event_log, sid, workspace = _run_pipeline(
        workspaces_root,
        [script[0], script[1], unsafe_code, script[3], safety_repair],
    )

    assert result.ok is True
    assert "safety_repair" in _purposes(event_log, sid)
    assert "eval(" not in (workspace / "generated" / "agent.py").read_text(encoding="utf-8")
    requested = next(
        event for event in event_log.read_all(sid)
        if event.kind == EventKind.DECISION_INPUT and event.payload.get("kind") == "safety_repair_requested"
    )
    assert requested.payload["unsafe_findings"] == {"generated/agent.py": ["eval()"]}
    assert requested.payload["unsafe_violation_context"][0]["path"] == "generated/agent.py"
    assert "eval(" in requested.payload["unsafe_violation_context"][0]["snippet"]
    safety_call = next(
        event for event in event_log.read_all(sid) if event.kind == EventKind.MODEL_CALLED and event.payload.get("purpose") == "safety_repair"
    )
    assert isinstance(safety_call.payload.get("duration_ms"), int)


def test_safety_repair_prompt_failure_is_clear_when_eval_persists(workspaces_root) -> None:
    fixture = json.loads(_FIXTURE_22C0C771_EVAL_SAFETY_FAILURE.read_text(encoding="utf-8"))
    script = _base_script()
    unsafe_code = _response(
        "unsafe-code-generation-22c0",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": fixture["generated_agent_source"],
                }
            ],
            "notes": "Unsafe eval-based agent.",
            "assumptions": [],
        },
    )
    unsafe_safety_repair = _response(
        "unsafe-safety-repair-22c0",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": fixture["safety_repair_candidate_source"],
                }
            ],
            "notes": "Still unsafe.",
            "assumptions": [],
        },
    )

    result, event_log, sid, _workspace = _run_pipeline(
        workspaces_root,
        [script[0], script[1], unsafe_code, script[3], unsafe_safety_repair],
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    assert "eval()" in (result.message or "")
    assert "path=generated/agent.py forbidden_construct=eval()" in (result.technical_detail or "")
    assert "Safety repair returned code that still contains forbidden constructs after recheck." in (
        result.technical_detail or ""
    )
    purposes = _purposes(event_log, sid)
    assert purposes.count("safety_repair") == 1
    assert purposes.count("json_repair") == 0


def test_safety_repair_json_repair_does_not_accept_eval_after_envelope_fix(workspaces_root) -> None:
    fixture = json.loads(_FIXTURE_22C0C771_EVAL_SAFETY_FAILURE.read_text(encoding="utf-8"))
    script = _base_script()
    unsafe_code = _response(
        "unsafe-code-generation-22c0-json",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": fixture["generated_agent_source"],
                }
            ],
            "notes": "Unsafe eval-based agent.",
            "assumptions": [],
        },
    )
    malformed_safety_repair = _response(
        "malformed-safety-repair-22c0",
        "```json\n"
        "{\n"
        '  "files": [\n'
        '    {"path": "generated/agent.py", "content": '
        + json.dumps(fixture["safety_repair_candidate_source"])
        + "}\n"
        "  ]\n"
        "```",
    )
    unsafe_json_repair = _response(
        "unsafe-json-repair-22c0",
        {
            "files": [
                {
                    "path": "generated/agent.py",
                    "content": fixture["safety_repair_candidate_source"],
                }
            ]
        },
    )

    result, event_log, sid, _workspace = _run_pipeline(
        workspaces_root,
        [script[0], script[1], unsafe_code, script[3], malformed_safety_repair, unsafe_json_repair],
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.AUTHOR_GENERATED_CODE_FAILED
    assert "eval()" in (result.message or "")
    assert "path=generated/agent.py forbidden_construct=eval()" in (result.technical_detail or "")
    purposes = _purposes(event_log, sid)
    assert purposes.count("safety_repair") == 1
    assert purposes.count("json_repair") == 1


def test_completion_gate_detects_non_llm_final_artifact_tampering(
    workspaces_root,
) -> None:
    script = _base_script()
    result, event_log, sid, workspace = _run_pipeline(workspaces_root, script)
    assert result.ok is True
    (workspace / "generated" / "agent.py").write_text(
        "# deterministic helper overwrote the model file\n",
        encoding="utf-8",
    )

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_gate",
        workspace=workspace,
    )

    assert error == ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED


def test_expense_policy_patch_sync_passes_completion_gate_hash_check(
    workspaces_root,
) -> None:
    script = _base_script()
    result, event_log, sid, workspace = _run_pipeline(workspaces_root, script)
    assert result.ok is True

    contract_path = workspace / "generated" / "author_output_contract.json"
    contract = AuthorOutputContract.model_validate(
        json.loads(contract_path.read_text(encoding="utf-8"))
    )
    patched = apply_expense_exception_golden_policy(
        contract.model_copy(
            update={
                "workflow_type": "expense_exception_review",
                "input_columns": ["expense_id", "amount", "policy_limit"],
                "output_columns": [
                    "expense_id",
                    "amount",
                    "policy_limit",
                    "exception_flag",
                    "exception_reason",
                ],
                "required_output_columns": ["expense_id", "exception_flag"],
            }
        )
    )

    contract_path.write_text(patched.model_dump_json(indent=2), encoding="utf-8")
    tamper_error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_gate",
        workspace=workspace,
    )
    assert tamper_error == ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED

    sync_author_output_contract_provenance(
        session_id=sid,
        event_log=event_log,
        workspace=workspace,
        contract=patched,
        step=3,
        stage_label="test_gate",
    )

    gate_error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=4,
        stage_label="test_gate",
        workspace=workspace,
    )
    assert gate_error is None
    assert patched.golden_comparison_requirement == "required"
    assert "review_required" in patched.required_output_columns


def test_completion_gate_detects_non_llm_contract_artifact_tampering(
    workspaces_root,
) -> None:
    script = _base_script()
    result, event_log, sid, workspace = _run_pipeline(workspaces_root, script)
    assert result.ok is True
    contract_path = workspace / "generated" / "author_output_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["workflow_type"] = "deterministic_helper_rewrite"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    error = enforce_author_completion_gate(
        session_id=sid,
        event_log=event_log,
        step=2,
        stage_label="test_gate",
        workspace=workspace,
    )

    assert error == ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED
