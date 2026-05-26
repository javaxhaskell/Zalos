#!/usr/bin/env python3
# ruff: noqa: E402,T201
"""Isolated codegen latency diagnostics against local Ollama.

Reuses a reviewed AuthorOutputContract from an existing workspace when
available, builds production-equivalent prompts (A/B/C), streams each
through ``OllamaModelClient.diagnose_stream``, and writes timing artifacts
under ``generated/debug/`` in the selected workspace.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

from agentforge.config import Settings
from agentforge.models.client import ModelMessage, TextBlock
from agentforge.models.ollama_client import OllamaModelClient, check_ollama_ready
from agentforge.orchestrator.author_llm_authoring import (
    _codegen_prompt,
    _compact_codegen_contract,
    _json_prompt,
    _parse_codegen_agent_source,
)
from agentforge.schemas.author_output_contract import AuthorOutputContract

_DEFAULT_USER_DESCRIPTION = (
    "Review uploaded expense CSV and produce row-level output plus a category summary."
)
_CODEGEN_SYSTEM_PROMPT = (
    "You write generated/agent.py for finance CSV agents. "
    "Return raw Python source only — no markdown, JSON, or explanation."
)
_BENCHMARK_MODELS = (
    "qwen2.5-coder:14b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
)
_RECOMMENDED_LOCAL_CODEGEN_MODEL = "qwen2.5-coder:7b"


def _resolve_path(value: str, repo_root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def _resolve_workspace(value: str, repo_root: Path) -> Path:
    path = _resolve_path(value, repo_root)
    if path.is_dir():
        return path
    for root in (repo_root / ".workspaces", repo_root / ".workspaces-blind-eval"):
        candidate = root / value
        if candidate.is_dir():
            return candidate
    raise SystemExit(f"Workspace not found: {value}")


def _load_contract_from_workspace(
    workspace: Path,
) -> tuple[Path, AuthorOutputContract, str]:
    for rel in (
        "generated/author_output_contract.json",
        "generated/model_contract_review.json",
    ):
        path = workspace / rel
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if rel.endswith("model_contract_review.json"):
            reviewed = payload.get("reviewed_contract")
            if not isinstance(reviewed, dict):
                continue
            payload = reviewed
        contract = AuthorOutputContract.model_validate(payload)
        workflow_type = str(payload.get("workflow_type") or "unknown")
        return workspace, contract, workflow_type
    raise SystemExit(f"No reviewed contract found under {workspace}")


def _load_contract_file(
    contract_path: Path,
) -> tuple[Path, AuthorOutputContract, str]:
    if not contract_path.is_file():
        raise SystemExit(f"Contract file not found: {contract_path}")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if isinstance(payload.get("reviewed_contract"), dict):
        payload = payload["reviewed_contract"]
    contract = AuthorOutputContract.model_validate(payload)
    workspace = (
        contract_path.parent.parent
        if contract_path.parent.name == "generated"
        else contract_path.parent
    )
    workflow_type = str(payload.get("workflow_type") or "unknown")
    return workspace, contract, workflow_type


def _find_contract(repo_root: Path) -> tuple[Path, AuthorOutputContract, str]:
    search_roots = (
        repo_root / ".workspaces",
        repo_root / ".workspaces-blind-eval",
    )
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/generated/author_output_contract.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            contract = AuthorOutputContract.model_validate(payload)
            workflow_type = str(payload.get("workflow_type") or "unknown")
            return path.parent.parent, contract, workflow_type
        for path in sorted(root.glob("*/generated/model_contract_review.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            reviewed = payload.get("reviewed_contract")
            if isinstance(reviewed, dict):
                contract = AuthorOutputContract.model_validate(reviewed)
                workflow_type = str(reviewed.get("workflow_type") or "unknown")
                return path.parent.parent, contract, workflow_type
    raise SystemExit("No reviewed contract found under .workspaces or .workspaces-blind-eval")


def _user_description_from_events(workspace: Path) -> str | None:
    events_path = workspace / "events.jsonl"
    if not events_path.is_file():
        return None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") == "author_user_workflow" and isinstance(payload.get("text"), str):
            return payload["text"]
    return None


def _prompt_b_minimal(
    *,
    workflow_type: str,
    user_description: str,
    contract: AuthorOutputContract,
) -> str:
    contract_json = _json_prompt(_compact_codegen_contract(contract))
    return "\n".join(
        [
            f"Workflow: {workflow_type}",
            "Write generated/agent.py as raw Python only.",
            "CLI: input CSV path and output CSV path.",
            f"Request: {user_description}",
            f"Contract JSON: {contract_json}",
        ]
    )


def _prompt_c_ultra_minimal(
    *,
    user_description: str,
    contract: AuthorOutputContract,
) -> str:
    contract_json = _json_prompt(_compact_codegen_contract(contract))
    return (
        "Write a Python script that reads this CSV and writes output/report "
        f"according to this compact contract.\n"
        f"User request: {user_description}\n"
        f"Contract: {contract_json}"
    )


async def _run_variant(
    *,
    client: OllamaModelClient,
    label: str,
    user_prompt: str,
    debug_dir: Path,
    max_tokens: int | None,
) -> dict[str, Any]:
    partial_path = debug_dir / f"codegen_partial_{label}.py"
    live_partial_path = (
        debug_dir / "codegen_partial.py" if label == "A_production" else partial_path
    )
    diagnostics = await client.diagnose_stream(
        system_prompt=_CODEGEN_SYSTEM_PROMPT,
        messages=[ModelMessage(role="user", content=[TextBlock(text=user_prompt)])],
        max_tokens=max_tokens,
        partial_output_path=live_partial_path,
    )
    partial_path.write_text(diagnostics.generated_text, encoding="utf-8")
    if label == "A_production":
        (debug_dir / "codegen_partial.py").write_text(
            diagnostics.generated_text,
            encoding="utf-8",
        )
    result = {
        "label": label,
        "prompt_chars": len(user_prompt),
        "prompt_lines": user_prompt.count("\n") + (1 if user_prompt else 0),
        "max_tokens_set": max_tokens is not None,
        "max_tokens": max_tokens,
        **asdict(diagnostics),
    }
    report_path = debug_dir / f"codegen_latency_{label}.json"
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _filename_safe_model(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def _looks_like_agent_py(source: str | None) -> bool:
    if source is None:
        return False
    lowered = source.lower()
    has_entrypoint = "def main" in source or "if __name__" in source
    has_cli = "sys.argv" in source or "argparse" in lowered
    has_file_io = "open(" in source or "csv" in lowered or "pandas" in lowered
    return has_entrypoint and has_cli and has_file_io


def _chars_per_second(*, generated_chars: int, elapsed_seconds: float) -> float | None:
    if elapsed_seconds <= 0:
        return None
    return round(generated_chars / elapsed_seconds, 2)


def _benchmark_recommendation(results: list[dict[str, Any]]) -> str:
    recommended_missing = next(
        (
            result
            for result in results
            if result.get("model") == _RECOMMENDED_LOCAL_CODEGEN_MODEL
            and result.get("installed") is False
        ),
        None,
    )
    if recommended_missing is not None:
        return "Install the recommended local codegen model: ollama pull qwen2.5-coder:7b"

    viable = [
        result
        for result in results
        if result.get("installed")
        and result.get("parses_as_python")
        and result.get("looks_like_agent_py")
        and not result.get("timed_out")
    ]
    if not viable:
        return "No benchmarked model produced a complete agent.py-like Python output."

    def _score(result: dict[str, Any]) -> float:
        tokens_per_second = result.get("tokens_per_second")
        if isinstance(tokens_per_second, int | float):
            return float(tokens_per_second)
        chars_per_second = result.get("chars_per_second")
        if isinstance(chars_per_second, int | float):
            return float(chars_per_second)
        return 0.0

    best = max(viable, key=_score)
    return f"Use {best['model']} for local codegen."


async def _run_model_benchmark(
    *,
    settings: Settings,
    models: list[str],
    user_prompt: str,
    debug_dir: Path,
) -> int:
    """Compare codegen models against the same reviewed-contract prompt."""
    results: list[dict[str, Any]] = []
    readiness: dict[str, tuple[bool, str]] = {}
    for model in models:
        readiness[model] = await check_ollama_ready(
            base_url=settings.ollama_base_url,
            model=model,
        )

    recommended_ready = readiness.get(_RECOMMENDED_LOCAL_CODEGEN_MODEL, (True, ""))[0]
    if _RECOMMENDED_LOCAL_CODEGEN_MODEL in models and not recommended_ready:
        detail = readiness[_RECOMMENDED_LOCAL_CODEGEN_MODEL][1]
        result = {
            "model": _RECOMMENDED_LOCAL_CODEGEN_MODEL,
            "installed": False,
            "detail": detail,
            "pull_command": "ollama pull qwen2.5-coder:7b",
        }
        results.append(result)
        summary = {
            "benchmark": "codegen_model_comparison",
            "prompt_chars": len(user_prompt),
            "models": results,
            "recommendation": _benchmark_recommendation(results),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        summary_path = debug_dir / "codegen_model_benchmark_summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Missing recommended model: {_RECOMMENDED_LOCAL_CODEGEN_MODEL}")
        print(f"  {detail}")
        print("Run: ollama pull qwen2.5-coder:7b")
        print(f"Wrote summary to {summary_path}")
        return 2

    for model in models:
        ok, detail = readiness[model]
        if not ok:
            results.append(
                {
                    "model": model,
                    "installed": False,
                    "detail": detail,
                    "pull_command": f"ollama pull {model}",
                }
            )
            print(f"Skipping missing model {model}: {detail}")
            continue

        client = OllamaModelClient(
            base_url=settings.ollama_base_url,
            model=model,
            timeout_seconds=settings.ollama_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
            temperature=settings.ollama_codegen_temperature,
        )
        partial_path = debug_dir / f"codegen_partial_{_filename_safe_model(model)}.py"
        print(f"Benchmarking {model} ({len(user_prompt)} chars)...", flush=True)
        diagnostics = await client.diagnose_stream(
            system_prompt=_CODEGEN_SYSTEM_PROMPT,
            messages=[ModelMessage(role="user", content=[TextBlock(text=user_prompt)])],
            max_tokens=None,
            partial_output_path=partial_path,
        )
        source, parse_error = _parse_codegen_agent_source(diagnostics.generated_text)
        result = {
            "model": model,
            "installed": True,
            "prompt_chars": len(user_prompt),
            "time_to_first_token_seconds": diagnostics.time_to_first_token_seconds,
            "generated_chars": diagnostics.generated_chars,
            "elapsed_seconds": diagnostics.total_elapsed_seconds,
            "tokens_per_second": diagnostics.tokens_per_second,
            "chars_per_second": _chars_per_second(
                generated_chars=diagnostics.generated_chars,
                elapsed_seconds=diagnostics.total_elapsed_seconds,
            ),
            "parses_as_python": source is not None,
            "parse_error": parse_error,
            "looks_like_agent_py": _looks_like_agent_py(source),
            "timed_out": diagnostics.timed_out,
            "error": diagnostics.error,
            "ollama_stats": diagnostics.ollama_stats,
        }
        results.append(result)
        report_path = debug_dir / f"codegen_model_benchmark_{_filename_safe_model(model)}.json"
        report_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"  TTFT={result['time_to_first_token_seconds']}s "
            f"elapsed={result['elapsed_seconds']}s "
            f"chars={result['generated_chars']} "
            f"tokens/sec={result['tokens_per_second']} "
            f"chars/sec={result['chars_per_second']} "
            f"python={result['parses_as_python']} "
            f"agent_py={result['looks_like_agent_py']}",
            flush=True,
        )

    summary = {
        "benchmark": "codegen_model_comparison",
        "prompt_chars": len(user_prompt),
        "models": results,
        "recommendation": _benchmark_recommendation(results),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    summary_path = debug_dir / "codegen_model_benchmark_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Recommendation: {summary['recommendation']}")
    print(f"Wrote summary to {summary_path}")
    return 0


async def main_async(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.contract:
        workspace_root, contract, workflow_type = _load_contract_file(
            _resolve_path(args.contract, _REPO_ROOT)
        )
    elif args.workspace:
        workspace_root, contract, workflow_type = _load_contract_from_workspace(
            _resolve_workspace(args.workspace, _REPO_ROOT)
        )
    else:
        workspace_root, contract, workflow_type = _find_contract(_REPO_ROOT)
    if args.workflow_type:
        workflow_type = args.workflow_type
    user_description = (
        args.user_description
        or _user_description_from_events(workspace_root)
        or _DEFAULT_USER_DESCRIPTION
    )
    custom_contract_interface = contract.build_mode == "llm_custom"

    prompt_a = _codegen_prompt(
        workflow_type=workflow_type,
        user_description=user_description,
        reviewed_contract=contract,
        custom_contract_interface=custom_contract_interface,
    )
    prompt_b = _prompt_b_minimal(
        workflow_type=workflow_type,
        user_description=user_description,
        contract=contract,
    )
    prompt_c = _prompt_c_ultra_minimal(
        user_description=user_description,
        contract=contract,
    )

    debug_dir = Path(args.output_dir) if args.output_dir else workspace_root / "generated" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "codegen_prompt.txt").write_text(prompt_a, encoding="utf-8")
    required_output_paths = contract.all_required_output_paths()
    client = OllamaModelClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_codegen_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        num_ctx=settings.ollama_num_ctx,
        temperature=settings.ollama_codegen_temperature,
    )
    options_sent = client.request_options(max_tokens=None)
    meta = {
        "prompt_chars": len(prompt_a),
        "prompt_lines": prompt_a.count("\n") + (1 if prompt_a else 0),
        "compact_contract_chars": len(_json_prompt(_compact_codegen_contract(contract))),
        "required_output_files": len(required_output_paths),
        "required_output_file_paths": required_output_paths,
        "output_format": "raw_python",
        "output_format_requested": "raw_python",
        "model": settings.ollama_codegen_model,
        "provider": "ollama",
        "temperature": settings.ollama_codegen_temperature,
        "num_ctx": settings.ollama_num_ctx,
        "stream": True,
        "options_sent_to_ollama": options_sent,
        "num_predict_set": "num_predict" in options_sent,
        "num_predict": options_sent.get("num_predict"),
        "max_tokens_set": False,
        "max_tokens": None,
        "timeout_seconds": settings.ollama_timeout_seconds,
        "contract_source": str(workspace_root),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (debug_dir / "codegen_prompt_meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if args.benchmark_models:
        models = args.models or list(_BENCHMARK_MODELS)
        return await _run_model_benchmark(
            settings=settings,
            models=models,
            user_prompt=prompt_a,
            debug_dir=debug_dir,
        )

    ok, detail = await check_ollama_ready(
        base_url=settings.ollama_base_url,
        model=settings.ollama_codegen_model,
    )
    if not ok:
        print(f"Ollama not ready: {detail}", file=sys.stderr)
        return 2

    variants = [
        ("A_production", prompt_a),
        ("B_minimal", prompt_b),
        ("C_ultra_minimal", prompt_c),
    ]
    if args.variant:
        variants = [item for item in variants if item[0].startswith(args.variant)]
        if not variants:
            print(f"Unknown variant filter: {args.variant}", file=sys.stderr)
            return 1

    results: list[dict[str, Any]] = []
    for label, prompt in variants:
        print(f"Running variant {label} ({len(prompt)} chars)...", flush=True)
        result = await _run_variant(
            client=client,
            label=label,
            user_prompt=prompt,
            debug_dir=debug_dir,
            max_tokens=None,
        )
        results.append(result)
        ttft = result.get("time_to_first_token_seconds")
        tps = result.get("tokens_per_second")
        print(
            f"  TTFT={ttft}s total={result['total_elapsed_seconds']}s "
            f"chars={result['generated_chars']} tokens/sec={tps} "
            f"streamed={result['streamed']} hung={result['hung_before_first_token']}",
            flush=True,
        )

    summary = {
        "contract_source": str(workspace_root),
        "model": settings.ollama_codegen_model,
        "timeout_seconds": settings.ollama_timeout_seconds,
        "variants": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    summary_path = debug_dir / "codegen_latency_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote summary to {summary_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        help="Directory for debug artifacts (default: contract workspace generated/debug)",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace path or session id containing generated/author_output_contract.json",
    )
    parser.add_argument(
        "--contract",
        help="Path to author_output_contract.json or model_contract_review.json",
    )
    parser.add_argument(
        "--variant",
        help="Run only variants whose label starts with this prefix (A, B, or C)",
    )
    parser.add_argument(
        "--workflow-type",
        help="Override workflow type embedded in the diagnostic prompts",
    )
    parser.add_argument(
        "--user-description",
        help="Override the user request text embedded in prompts",
    )
    parser.add_argument(
        "--benchmark-models",
        action="store_true",
        help=(
            "Benchmark qwen2.5-coder:14b, qwen2.5-coder:7b, and "
            "qwen2.5-coder:3b when installed against the production prompt."
        ),
    )
    parser.add_argument(
        "--models",
        nargs="+",
        help="Override benchmark model list.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
