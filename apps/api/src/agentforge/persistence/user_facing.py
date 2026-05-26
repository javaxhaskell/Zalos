"""Sanitise session artifacts for user-facing export and README rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from agentforge.schemas import BudgetStatus, EventKind
from agentforge.schemas.author_output_contract import AuthorOutputContract

_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w./-])/(?:Users|home|var|tmp|private|opt)/[^\s\"'`<>|]+"
)


def relativize_workspace_path(*, session_id: UUID | str, absolute_path: str) -> str:
    """Replace an absolute workspace path with a portable relative label."""
    sid = str(session_id)
    marker = f"/.workspaces/{sid}"
    if marker in absolute_path:
        return f".workspaces/{sid}"
    if absolute_path.endswith(sid):
        return f".workspaces/{sid}"
    return ".workspaces/<session_id>"


def sanitize_user_facing_text(
    text: str,
    *,
    workspace: Path | None = None,
    session_id: UUID | str | None = None,
) -> str:
    """Strip absolute filesystem paths from text shown to users."""
    if not text:
        return text
    sanitized = text
    if workspace is not None:
        workspace_str = str(workspace.resolve())
        sanitized = sanitized.replace(workspace_str, ".")
        parent = workspace.parent.resolve()
        sanitized = sanitized.replace(str(parent), ".")
    if session_id is not None:
        sid = str(session_id)
        sanitized = sanitized.replace(f"/.workspaces/{sid}", f".workspaces/{sid}")
    sanitized = _ABSOLUTE_PATH_PATTERN.sub("<workspace-path>", sanitized)
    return sanitized


_FALLBACK_WORKFLOW_REPORT_PATHS: tuple[str, ...] = ("reports/validation_report.md",)


def sanitize_user_facing_workflow_reports(
    *,
    workspace: Path,
    session_id: UUID | str,
    contract: AuthorOutputContract | None = None,
) -> list[str]:
    """Strip absolute paths from model-authored workflow markdown reports."""
    from agentforge.orchestrator.workflow_artifacts import workflow_report_path_from_contract

    rel_paths: set[str] = set(_FALLBACK_WORKFLOW_REPORT_PATHS)
    if contract is not None:
        primary_report = workflow_report_path_from_contract(contract)
        if primary_report:
            rel_paths.add(primary_report)
        for rel_path in contract.all_required_output_paths():
            if rel_path.lower().endswith(".md"):
                rel_paths.add(rel_path)

    changed: list[str] = []
    for rel_path in sorted(rel_paths):
        report_path = workspace / rel_path
        if not report_path.is_file():
            continue
        raw = report_path.read_text(encoding="utf-8")
        sanitized = sanitize_user_facing_text(
            raw,
            workspace=workspace,
            session_id=session_id,
        )
        if sanitized != raw:
            report_path.write_text(sanitized, encoding="utf-8")
            changed.append(rel_path)
    return changed


def sanitize_manifest_for_export(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *manifest* safe to ship in downloadable archives."""
    exported = dict(manifest)
    session_id = exported.get("session_id")
    workspace_path = exported.get("workspace_path")
    if isinstance(session_id, str) and isinstance(workspace_path, str):
        exported["workspace_path"] = relativize_workspace_path(
            session_id=session_id,
            absolute_path=workspace_path,
        )
    return exported


def write_export_manifest(workspace: Path, manifest: dict[str, Any]) -> None:
    """Write a sanitised manifest snapshot used only inside export zips."""
    export_path = workspace / "manifest.export.json"
    export_path.write_text(
        json.dumps(sanitize_manifest_for_export(manifest), indent=2),
        encoding="utf-8",
    )


def budget_tokens_from_events(events: list[Any]) -> int:
    """Aggregate token usage from ``MODEL_CALLED`` events."""
    total = 0
    for event in events:
        if event.kind != EventKind.MODEL_CALLED:
            continue
        usage = event.payload.get("usage") or {}
        if isinstance(usage.get("total_tokens"), int):
            total += int(usage["total_tokens"])
            continue
        total += int(usage.get("input_tokens") or 0)
        total += int(usage.get("output_tokens") or 0)
    return total


def budget_status_from_events(events: list[Any]) -> BudgetStatus:
    """Aggregate live budget counters from a session event log."""
    tokens_used = budget_tokens_from_events(events)
    tool_calls_used = sum(1 for event in events if event.kind == EventKind.TOOL_INVOKED)
    max_step = max(
        (event.step for event in events if isinstance(event.step, int)),
        default=-1,
    )
    steps_used = max_step + 1 if max_step >= 0 else 0
    wall_seconds_used = 0
    if events:
        wall_seconds_used = max(
            0,
            int((events[-1].ts - events[0].ts).total_seconds()),
        )
    return BudgetStatus(
        tokens_used=tokens_used,
        tool_calls_used=tool_calls_used,
        steps_used=steps_used,
        wall_seconds_used=wall_seconds_used,
    )


def sync_manifest_budget_from_events(
    *,
    workspace_manager: Any,
    session_id: UUID,
    events: list[Any],
) -> BudgetStatus:
    """Mirror event-derived budget counters into ``manifest.json``."""
    budget = budget_status_from_events(events)
    workspace_manager.update_manifest(
        session_id,
        budget=budget.model_dump(mode="json"),
    )
    return budget


def effective_budget_display(
    completion: dict[str, Any],
    budget: dict[str, Any],
) -> dict[str, int]:
    """Budget counters for README display, reconciled with completion provenance."""
    completion_tokens = int(completion.get("tokens_used") or 0)
    manifest_tokens = int(budget.get("tokens_used") or 0)
    tokens_used = max(completion_tokens, manifest_tokens)
    return {
        "tokens_used": tokens_used,
        "tool_calls_used": int(budget.get("tool_calls_used") or 0),
        "steps_used": int(budget.get("steps_used") or 0),
        "wall_seconds_used": int(budget.get("wall_seconds_used") or 0),
    }


__all__ = [
    "budget_status_from_events",
    "budget_tokens_from_events",
    "effective_budget_display",
    "relativize_workspace_path",
    "sanitize_manifest_for_export",
    "sanitize_user_facing_text",
    "sanitize_user_facing_workflow_reports",
    "sync_manifest_budget_from_events",
    "write_export_manifest",
]
