"""Workflow deliverable vs system validation report helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agentforge.schemas.artifact import ArtifactType
from agentforge.schemas.author_output_contract import AuthorOutputContract

_SYSTEM_VALIDATION_REPORT_CANDIDATES = (
    "reports/system_validation_report.md",
    "reports/agentforge_system_validation_report.md",
    "audit/system_validation_report.md",
)


def choose_system_validation_report_path(required_paths: set[str]) -> str:
    """Pick a system validation report path that never collides with workflow deliverables."""
    for candidate in _SYSTEM_VALIDATION_REPORT_CANDIDATES:
        if candidate not in required_paths:
            return candidate
    return _SYSTEM_VALIDATION_REPORT_CANDIDATES[-1]


def system_validation_report_json_path(md_rel: str) -> str:
    """JSON sidecar path alongside the system validation markdown report."""
    if md_rel.endswith(".md"):
        return md_rel[: -len(".md")] + ".json"
    return md_rel + ".json"


def artifact_type_for_required_path(path: str) -> str:
    """Map a contract-required workflow path to an artifact event type."""
    normalized = path.replace("\\", "/")
    if normalized.endswith(".md"):
        return ArtifactType.WORKFLOW_REPORT.value
    if normalized.endswith(".csv"):
        if normalized.startswith("outputs/"):
            return ArtifactType.WORKFLOW_ROW_OUTPUT.value
        if "summary_by_" in normalized:
            return "summary_csv"
        if normalized.endswith("exceptions.csv"):
            return "exceptions_csv"
        return ArtifactType.WORKFLOW_ROW_OUTPUT.value
    return ArtifactType.OUTPUT_FILE.value


def normalize_artifact_type(artifact_type: str) -> str:
    """Map legacy validation_report events to system_validation_report."""
    if artifact_type == ArtifactType.VALIDATION_REPORT.value:
        return ArtifactType.SYSTEM_VALIDATION_REPORT.value
    return artifact_type


def snapshot_required_workflow_artifacts(
    workspace: Path,
    contract: AuthorOutputContract,
) -> dict[str, str]:
    """Return path -> sha256 for every required workflow artifact that exists."""
    snapshots: dict[str, str] = {}
    for rel_path in contract.all_required_output_paths():
        artifact_abs = workspace / rel_path
        if artifact_abs.is_file():
            snapshots[rel_path] = hashlib.sha256(artifact_abs.read_bytes()).hexdigest()
    return snapshots


def verify_workflow_artifacts_preserved(
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    """Return required workflow paths whose content hash changed."""
    changed: list[str] = []
    for path, hash_before in before.items():
        if after.get(path) != hash_before:
            changed.append(path)
    return changed


def workflow_report_path_from_contract(contract: AuthorOutputContract) -> str | None:
    """Primary user-facing markdown report path declared by the contract, if any."""
    for rel_path in contract.all_required_output_paths():
        if rel_path.lower().endswith(".md"):
            return rel_path
    for rel_path in contract.all_declared_output_paths():
        if rel_path.lower().endswith(".md"):
            return rel_path
    return None


__all__ = [
    "artifact_type_for_required_path",
    "choose_system_validation_report_path",
    "normalize_artifact_type",
    "snapshot_required_workflow_artifacts",
    "system_validation_report_json_path",
    "verify_workflow_artifacts_preserved",
    "workflow_report_path_from_contract",
]
