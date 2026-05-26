#!/usr/bin/env python3
"""Run Author blind evaluation cases with integrity checks.

Loads cases from ``blind_eval_cases/``, drives the Author custom-build
pipeline programmatically, records build mode and model calls, runs
independent oracles when a case completes, and writes JSON + Markdown
reports under ``reports/``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

from agentforge.config import Settings
from agentforge.models import (
    AnthropicModelClient,
    DeepSeekModelClient,
    FakeModelClient,
    ModelClientError,
    ModelResponse,
    OllamaModelClient,
    TextBlock,
)
from agentforge.orchestrator.author_blind_eval import (
    BlindEvalIntegrityError,
    assert_blind_eval_completion_integrity,
    git_provenance,
)
from agentforge.orchestrator.author_custom_build import (
    UploadedDataFile,
    execute_custom_workflow_pipeline,
)
from agentforge.orchestrator.author_llm_authoring import (
    author_provenance_from_events,
    collect_author_model_stages,
    count_author_model_calls,
    model_contributed_files_from_events,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ErrorCode, SessionStatus, Workflow

_CASES_ROOT = _REPO_ROOT / "blind_eval_cases"
_REPORTS_DIR = _REPO_ROOT / "reports"
_MODEL_ACK = "AUTHOR_MODEL_CONTRIBUTION_ACK"


@dataclass
class BlindCaseResult:
    case_id: str
    passed: bool
    skipped: bool
    expected_status: str
    actual_status: str
    expected_error: str | None
    actual_error: str | None
    build_mode: str | None
    model_calls: int
    tokens_used: int
    provider: str | None
    model: str | None
    model_stages: list[str]
    generated_files: list[str]
    validation_result: str | None
    archive_path: str | None
    workflow_type: str | None
    oracle_passed: bool | None
    oracle_message: str | None
    failure_reason: str | None


def _load_case_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "case.json").is_file())


def _load_case(case_dir: Path) -> dict[str, Any]:
    return json.loads((case_dir / "case.json").read_text(encoding="utf-8"))


def _load_oracle(case_dir: Path):
    oracle_path = case_dir / "oracle.py"
    if not oracle_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"blind_oracle_{case_dir.name}", oracle_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _status_name(status: SessionStatus) -> str:
    return status.name.lower()


def _error_name(error: ErrorCode | None) -> str | None:
    return error.value if error is not None else None


def _extract_build_mode(events: list[Any]) -> str | None:
    for event in reversed(events):
        if event.kind.value != "decision_input":
            continue
        payload = event.payload or {}
        if payload.get("kind") == "author_output_contract":
            return payload.get("build_mode")
    return None


def _extract_workflow_type(events: list[Any]) -> str | None:
    for event in reversed(events):
        if event.kind.value != "decision_input":
            continue
        payload = event.payload or {}
        if payload.get("kind") == "custom_workflow_output_contract":
            return payload.get("workflow_type")
    return None


def _build_real_model_client(settings: Settings):
    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        return OllamaModelClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
    if provider == "anthropic":
        if not settings.anthropic_api_key.startswith("sk-ant-"):
            raise ModelClientError(
                "LLM_PROVIDER=anthropic requires a real ANTHROPIC_API_KEY for --real-model"
            )
        return AnthropicModelClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model_primary,
            max_retries=settings.anthropic_max_retries,
        )
    if provider == "deepseek":
        if not settings.deepseek_api_key.strip():
            raise ModelClientError(
                "LLM_PROVIDER=deepseek requires a real DEEPSEEK_API_KEY for --real-model"
            )
        return DeepSeekModelClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
            temperature=settings.deepseek_temperature,
            max_retries=settings.deepseek_max_retries,
        )
    raise ModelClientError(f"Unsupported LLM_PROVIDER for --real-model: {settings.llm_provider}")


def _read_completion(workspace: Path) -> dict[str, Any]:
    manifest_path = workspace / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    completion = manifest.get("completion")
    return completion if isinstance(completion, dict) else {}


def _run_case(
    *,
    case_dir: Path,
    settings: Settings,
    workspaces_root: Path,
    real_model: bool,
) -> BlindCaseResult:
    meta = _load_case(case_dir)
    case_id = str(meta["id"])
    prompt = (case_dir / str(meta["prompt_file"])).read_text(encoding="utf-8").strip()
    input_name = str(meta["input_file"])
    upload_format = str(meta.get("upload_format", "csv"))
    expected_status = str(meta["expected_status"])
    expected_error = meta.get("expected_error")
    allowed_build_modes = list(meta.get("allowed_build_modes") or [])
    use_model = bool(meta.get("use_model", False))
    requires_real_model = bool(meta.get("requires_real_model", False))
    min_model_calls = int(meta.get("min_model_calls", 0))
    workflow_type_override = meta.get("workflow_type")

    if requires_real_model and not real_model:
        return BlindCaseResult(
            case_id=case_id,
            passed=True,
            skipped=True,
            expected_status=expected_status,
            actual_status="skipped",
            expected_error=str(expected_error) if expected_error else None,
            actual_error=None,
            build_mode=None,
            model_calls=0,
            tokens_used=0,
            provider=None,
            model=None,
            model_stages=[],
            generated_files=[],
            validation_result=None,
            archive_path=None,
            workflow_type=str(workflow_type_override) if workflow_type_override else None,
            oracle_passed=None,
            oracle_message="Skipped: case requires --real-model.",
            failure_reason=None,
        )

    wm = WorkspaceManager(root=workspaces_root)
    session_id = uuid4()
    wm.allocate(session_id, Workflow.AUTHOR)
    workspace = wm.get(session_id)
    upload_path = workspace / "uploads" / input_name
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes((case_dir / input_name).read_bytes())
    event_log = EventLog(wm)

    model_client = None
    if use_model:
        if real_model:
            model_client = _build_real_model_client(settings)
        else:
            model_client = FakeModelClient(
                script=[
                    ModelResponse(
                        id=f"blind-model-{idx}",
                        content=[TextBlock(text=_MODEL_ACK)],
                        stop_reason="end_turn",
                    )
                    for idx in range(4)
                ]
            )

    workflow_type = workflow_type_override
    status, error = asyncio.run(
        execute_custom_workflow_pipeline(
            session_id=session_id,
            workspace=workspace,
            upload=UploadedDataFile(path=upload_path, format=upload_format),
            workflow_type=workflow_type,
            user_description=prompt,
            settings=settings,
            event_log=event_log,
            workspace_manager=wm,
            step=1,
            model_client=model_client,
        )
    )
    events = event_log.read_all(session_id)
    model_calls = count_author_model_calls(events)
    provenance = author_provenance_from_events(events)
    tokens_used = int(provenance.get("tokens_used") or 0)
    provider = provenance.get("llm_provider")
    model = provenance.get("llm_model")
    model_stages = collect_author_model_stages(events)
    generated_files = model_contributed_files_from_events(events)
    build_mode = _extract_build_mode(events)
    workflow_type_observed = _extract_workflow_type(events) or workflow_type_override
    completion = _read_completion(workspace)
    validation_result = completion.get("validation_overall")
    archive_path = str(workspace / "archive.zip") if (workspace / "archive.zip").is_file() else None

    failure_reason: str | None = None
    actual_status = _status_name(status)
    actual_error = _error_name(error)

    if actual_status != expected_status:
        failure_reason = f"expected status {expected_status}, got {actual_status}"
    elif expected_error and actual_error != expected_error:
        failure_reason = f"expected error {expected_error}, got {actual_error}"
    elif build_mode and allowed_build_modes and build_mode not in allowed_build_modes:
        failure_reason = f"build_mode {build_mode!r} not in allowlist {allowed_build_modes}"
    elif model_calls < min_model_calls:
        failure_reason = f"expected at least {min_model_calls} model calls, got {model_calls}"

    if failure_reason is None and status == SessionStatus.COMPLETED:
        completion_via = "ai_authored_workflow_build"
        try:
            assert_blind_eval_completion_integrity(
                settings=settings,
                events=events,
                workflow_type=workflow_type_observed or "unknown",
                build_mode=build_mode or "unknown",
                completion_via=completion_via,
                model_calls=model_calls,
            )
        except BlindEvalIntegrityError as exc:
            failure_reason = str(exc)

    oracle_passed: bool | None = None
    oracle_message: str | None = None
    oracle = _load_oracle(case_dir)
    if status == SessionStatus.COMPLETED and oracle is not None and hasattr(oracle, "run_oracle"):
        oracle_passed, oracle_message = oracle.run_oracle(
            workspace=workspace,
            input_path=upload_path,
        )
        if not oracle_passed:
            failure_reason = oracle_message or "Independent oracle failed."

    passed = failure_reason is None
    return BlindCaseResult(
        case_id=case_id,
        passed=passed,
        skipped=False,
        expected_status=expected_status,
        actual_status=actual_status,
        expected_error=str(expected_error) if expected_error else None,
        actual_error=actual_error,
        build_mode=build_mode,
        model_calls=model_calls,
        tokens_used=tokens_used,
        provider=str(provider) if provider else None,
        model=str(model) if model else None,
        model_stages=model_stages,
        generated_files=generated_files,
        validation_result=str(validation_result) if validation_result else None,
        archive_path=archive_path,
        workflow_type=workflow_type_observed,
        oracle_passed=oracle_passed,
        oracle_message=oracle_message,
        failure_reason=failure_reason,
    )


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Author blind evaluation report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Provenance",
        "",
        f"- Git commit: `{report['provenance']['git_commit']}`",
        f"- Git dirty: `{report['provenance']['git_dirty']}`",
        "- Author build path: `AI-authored workflow build`",
        "",
        f"## Summary: {report['passed']}/{report['total']} passed",
        "",
        "| Case | Pass | Status | Error | Provider/model | Calls/tokens | Stages | Oracle | Archive | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in report["cases"]:
        lines.append(
            "| {case_id} | {pass_mark} | {actual_status} | {actual_error} | {provider_model} | "
            "{calls_tokens} | {stages} | {oracle} | {archive} | {notes} |".format(
                case_id=result["case_id"],
                pass_mark="skip" if result.get("skipped") else ("yes" if result["passed"] else "no"),
                actual_status=result["actual_status"],
                actual_error=result["actual_error"] or "—",
                provider_model=(
                    f"{result['provider']}/{result['model']}"
                    if result.get("provider") or result.get("model")
                    else "—"
                ),
                calls_tokens=f"{result['model_calls']}/{result.get('tokens_used', 0)}",
                stages=", ".join(result.get("model_stages") or []) or "—",
                oracle=(
                    "pass"
                    if result.get("oracle_passed") is True
                    else ("fail" if result.get("oracle_passed") is False else "—")
                ),
                archive=result.get("archive_path") or "—",
                notes=result["failure_reason"] or "ok",
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", dest="case_id", help="Run a single blind case id.")
    parser.add_argument(
        "--real-model",
        action="store_true",
        help="Use the configured real model client instead of the fake failure script.",
    )
    args = parser.parse_args()

    settings = Settings(
        workspaces_root=_REPO_ROOT / ".workspaces-blind-eval",
        templates_root=_REPO_ROOT / "templates",
        author_blind_eval_mode=True,
    )
    settings.workspaces_root.mkdir(parents=True, exist_ok=True)

    case_dirs = _load_case_dirs(_CASES_ROOT)
    if args.case_id:
        case_dirs = [
            case_dir
            for case_dir in case_dirs
            if _load_case(case_dir).get("id") == args.case_id
        ]
    if not case_dirs:
        print(f"No blind cases found under {_CASES_ROOT}", file=sys.stderr)
        return 1

    results: list[BlindCaseResult] = []
    for case_dir in case_dirs:
        results.append(
            _run_case(
                case_dir=case_dir,
                settings=settings,
                workspaces_root=settings.workspaces_root,
                real_model=args.real_model,
            )
        )

    provenance = git_provenance()
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "author_blind_eval_mode": True,
        "real_model": args.real_model,
        "provenance": {
            **provenance,
            "author_requires_model_contribution": True,
        },
        "total": len(results),
        "passed": sum(1 for result in results if result.passed and not result.skipped),
        "skipped": sum(1 for result in results if result.skipped),
        "cases": [asdict(result) for result in results],
    }

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = _REPORTS_DIR / "author_blind_eval_report.json"
    md_path = _REPORTS_DIR / "author_blind_eval_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Passed {report['passed']}/{report['total']} (skipped {report['skipped']})")
    return 0 if report["passed"] + report["skipped"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
