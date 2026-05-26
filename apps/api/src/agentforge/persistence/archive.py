"""Workspace archive builder (BP10a).

Bundles every load-bearing artifact in a session workspace into a
single ``archive.zip`` the user can download from the wizard. The
archive is the audit-grade deliverable from a session: someone
auditing this submission can untar it and reconstruct everything the
agent did + everything the agent produced.

Shared by:

  * The ``archive_workspace`` tool — the model's path to building the
    archive while the BUILD/FIX phase is still running.
  * The ``GET /sessions/{id}/archive.zip`` HTTP endpoint — the
    user-driven path that builds the archive on demand if the tool
    didn't run.

Contents (in sorted order for stable hashing):

  * ``manifest.json`` — top-level
  * ``events.jsonl`` — top-level
  * ``uploads/**`` — the user's input data (so the archive is a
    self-contained reproduction kit; operators can re-run the agent
    against the same input).
  * ``generated/**``, ``working/**``, ``outputs/**``, ``reports/**``
    — recursive.

Excluded everywhere: ``__pycache__``, ``.pytest_cache``,
``.mypy_cache``, ``.ruff_cache``, ``.DS_Store``, ``Thumbs.db``,
``*.pyc`` (macOS / editor / Python detritus that has no place in an
auditable artifact).

Before zipping, this module stages a ``generated/data/`` directory
containing the user's uploaded input + the staged golden output (when
available) so the bundled ``generated/tests/`` directory is runnable
from the unpacked archive without further setup.

The archive itself is written to ``${workspace}/archive.zip`` and is
NOT included in subsequent rebuilds (we exclude self).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from agentforge.persistence.workspace import WorkspaceError, WorkspaceManager
from agentforge.persistence.user_facing import (
    effective_budget_display,
    sanitize_manifest_for_export,
)

_logger = logging.getLogger("agentforge.persistence.archive")

ARCHIVE_FILENAME = "archive.zip"

_INCLUDED_TOP_LEVEL_FILES: tuple[str, ...] = (
    "manifest.json",
    "events.jsonl",
    "SESSION_README.md",
)
_INCLUDED_TOP_LEVEL_DIRS: tuple[str, ...] = (
    "uploads",
    "generated",
    "working",
    "outputs",
    "reports",
)
_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
)
_EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {".DS_Store", "Thumbs.db", ".gitignore"}
)
_EXCLUDED_FILE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")


@dataclass(frozen=True)
class ArchiveResult:
    """Outcome of ``build_archive`` — same shape as the tool's output."""

    relative_path: str
    """Always ``"archive.zip"`` today; carried explicitly so callers
    don't hard-code it."""
    absolute_path: Path
    size_bytes: int
    hash_sha256: str
    file_count: int


def build_archive(
    session_id: UUID,
    workspace_manager: WorkspaceManager,
) -> ArchiveResult:
    """Build (or rebuild) ``${workspace}/archive.zip``.

    Idempotent: re-running overwrites the file. Pre-zip, we stage the
    user's uploaded input(s) + the staged golden into
    ``generated/data/`` so the unpacked archive is runnable against
    the bundled tests without further configuration.

    The ZIP itself uses normalised per-entry mtimes so the file-level
    sha256 is reproducible across runs on identical input.
    """
    workspace = workspace_manager.get(session_id)
    archive_path = workspace / ARCHIVE_FILENAME
    # Always rebuild from a clean file — partial archives from a
    # crash-mid-write would otherwise stick around.
    if archive_path.exists():
        archive_path.unlink()

    _stage_generated_data(workspace)
    _write_session_readme(workspace)

    entries = sorted(_iter_archive_entries(workspace))
    if not entries:
        raise WorkspaceError(
            "refusing to build an empty archive; workspace has none of "
            f"{_INCLUDED_TOP_LEVEL_FILES + _INCLUDED_TOP_LEVEL_DIRS}"
        )

    # Normalised mtime (2026-05-22T00:00:00Z → ZIP timestamp tuple).
    pinned_date = (2026, 5, 22, 0, 0, 0)
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as zf:
        for src_abs, arcname in entries:
            info = zipfile.ZipInfo(filename=arcname, date_time=pinned_date)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            payload = src_abs.read_bytes()
            if arcname == "manifest.json":
                try:
                    manifest = json.loads(payload)
                    payload = json.dumps(
                        sanitize_manifest_for_export(manifest),
                        indent=2,
                    ).encode("utf-8")
                except json.JSONDecodeError:
                    pass
            zf.writestr(info, payload)

    size = archive_path.stat().st_size
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    _logger.info(
        "archive built",
        extra={
            "session_id": str(session_id),
            "file_count": len(entries),
            "size_bytes": size,
        },
    )
    return ArchiveResult(
        relative_path=ARCHIVE_FILENAME,
        absolute_path=archive_path,
        size_bytes=size,
        hash_sha256=digest,
        file_count=len(entries),
    )


def _iter_archive_entries(workspace: Path) -> list[tuple[Path, str]]:
    """Yield ``(absolute_source_path, archive_relative_name)`` pairs.

    Sorted in the caller for deterministic ordering. The arcname is
    forward-slashed regardless of host OS so cross-platform unzip
    behaves predictably. Excludes editor / OS / Python build detritus
    that has no place in an audit artifact.
    """
    entries: list[tuple[Path, str]] = []
    # Top-level files.
    for name in _INCLUDED_TOP_LEVEL_FILES:
        candidate = workspace / name
        if candidate.is_file() and _include_file(candidate):
            entries.append((candidate, name))
    # Recursive top-level dirs.
    for dirname in _INCLUDED_TOP_LEVEL_DIRS:
        root = workspace / dirname
        if not root.is_dir():
            continue
        for src in root.rglob("*"):
            if not src.is_file():
                continue
            if any(part in _EXCLUDED_DIR_NAMES for part in src.parts):
                continue
            if not _include_file(src):
                continue
            arcname = str(src.relative_to(workspace)).replace("\\", "/")
            entries.append((src, arcname))
    return entries


def _include_file(path: Path) -> bool:
    if path.name in _EXCLUDED_FILE_NAMES:
        return False
    return path.suffix not in _EXCLUDED_FILE_SUFFIXES


def _stage_generated_data(workspace: Path) -> None:
    """Stage ``generated/data/`` for self-contained reproducibility.

    Model-authored ``generated/tests/`` commonly reads
    ``generated/data/sample_input.csv``. The user's actual upload lives in
    ``uploads/``; copy it into ``generated/data/`` so the downloaded archive is
    runnable as-is. Independent golden files, when supplied, remain eval
    artifacts and are copied only for reproducible checking.
    """
    generated = workspace / "generated"
    if not generated.is_dir():
        return
    data_dir = generated / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    uploads = workspace / "uploads"
    if uploads.is_dir():
        csv_uploads = sorted(
            p for p in uploads.iterdir()
            if p.is_file() and p.suffix.lower() == ".csv" and _include_file(p)
        )
        if csv_uploads:
            primary = csv_uploads[0]
            target = data_dir / "sample_input.csv"
            if not target.exists() or target.read_bytes() != primary.read_bytes():
                shutil.copy2(primary, target)
            for extra in csv_uploads[1:]:
                extra_target = data_dir / extra.name
                if not extra_target.exists():
                    shutil.copy2(extra, extra_target)

    golden_src = workspace / "evals" / "golden_output.csv"
    if golden_src.is_file():
        golden_target = data_dir / "golden_output.csv"
        if not golden_target.exists() or golden_target.read_bytes() != golden_src.read_bytes():
            shutil.copy2(golden_src, golden_target)


def _write_session_readme(workspace: Path) -> None:
    """Render ``SESSION_README.md`` from manifest + events.

    The README is a one-page reproducibility kit: how to reproduce
    output.csv, how to run tests, which validation layers fired,
    which model/provider was used, and whether recovery was used.

    Read by operators who don't want to grep the audit log.
    """
    manifest_path = workspace / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return

    completion: dict[str, Any] = manifest.get("completion") or {}
    workflow = manifest.get("workflow", "author")
    via = completion.get("completion_via")
    build_mode = completion.get("build_mode")
    if workflow == "repair" or via == "repair_validated_patch":
        _write_repair_session_readme(workspace, manifest, completion)
    elif build_mode in {"llm_custom", "llm_authoring"} or via in {
        "ai_authored_workflow_build",
        "model_planned_workflow_build",
    }:
        _write_custom_workflow_session_readme(workspace, manifest, completion)
    else:
        _write_author_session_readme(workspace, manifest, completion)


def _write_author_session_readme(
    workspace: Path,
    manifest: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    workflow_type = completion.get("workflow_type") or "model_authored_finance_workflow"
    provider = completion.get("llm_provider") or "(unknown)"
    model = completion.get("llm_model") or "(unknown)"
    base_url = completion.get("llm_base_url") or ""
    model_calls = completion.get("model_call_count", 0)
    tokens_used = completion.get("tokens_used", 0)
    model_files = completion.get("model_contributed_files") or []
    provenance_artifacts = completion.get("authoring_provenance_artifacts") or []
    tests_passed = completion.get("tests_passed")
    validation_passed = completion.get("validation_passed")
    via = completion.get("completion_via") or manifest.get("status", "unknown")
    recovery = bool(completion.get("recovery_used", False))
    overall = completion.get("validation_overall") or "unknown"
    output_path = completion.get("output_path") or "outputs/output.csv"
    output_hash = completion.get("output_hash") or ""
    report_path = completion.get("validation_report_path") or "reports/system_validation_report.md"
    report_hash = completion.get("validation_report_hash") or ""
    artifacts = completion.get("artifacts") or []
    validation_summary = completion.get("validation_summary") or []
    budget = manifest.get("budget") or {}
    budget_display = effective_budget_display(completion, budget)
    session_id = manifest.get("session_id", "")
    workflow = manifest.get("workflow", "author")
    started_at = manifest.get("started_at", "")
    updated_at = manifest.get("updated_at", "")

    lines: list[str] = []
    lines.append(f"# AgentForge session — {session_id}")
    lines.append("")
    lines.append(
        f"Workflow: **{workflow}** · Status: **{manifest.get('status', 'unknown')}** · "
        f"Started: {started_at} · Updated: {updated_at}"
    )
    lines.append("")
    lines.append("## How this was built")
    lines.append("")
    lines.append(f"- Build path: **AI-authored workflow build**")
    lines.append(f"- Workflow type: **{str(workflow_type).replace('_', ' ')}**")
    lines.append(f"- Completion path: `{via}`")
    lines.append(f"- Model repair used: **{'yes' if recovery else 'no'}**")
    lines.append("")
    lines.append("## LLM provider")
    lines.append("")
    lines.append(f"- Provider: `{provider}`")
    lines.append(f"- Model: `{model}`")
    if base_url:
        lines.append(f"- Base URL: `{base_url}`")
    lines.append(f"- Model calls (across pause+resume): **{model_calls}**")
    lines.append(f"- Tokens used: **{tokens_used}**")
    if model_files:
        lines.append("- Model-authored files:")
        for path in model_files:
            lines.append(f"  - `{path}`")
    if provenance_artifacts:
        lines.append("- Provenance artifacts:")
        for path in provenance_artifacts:
            lines.append(f"  - `{path}`")
    lines.append(f"- Generated tests passed: **{'yes' if tests_passed else 'no'}**")
    lines.append(f"- Validation passed: **{'yes' if validation_passed else 'no'}**")
    lines.append(
        f"- Budget on completion: tokens={budget_display['tokens_used']} / "
        f"tool_calls={budget_display['tool_calls_used']} / "
        f"steps={budget_display['steps_used']} / "
        f"wall_seconds={budget_display['wall_seconds_used']}"
    )
    lines.append("")
    lines.append("## How to reproduce `outputs/output.csv`")
    lines.append("")
    workflow_report = completion.get("workflow_report_path") or "reports/validation_report.md"
    input_path = completion.get("input_file") or completion.get("normalized_input_path") or "uploads/sample_input.csv"
    lines.append("```bash")
    lines.append("# from the unpacked archive root")
    lines.append(
        f"python3 generated/agent.py --input {input_path} "
        f"--contract generated/author_output_contract.json "
        f"--row-output {output_path} --report-path {workflow_report}"
    )
    lines.append("```")
    lines.append("")
    if output_hash:
        lines.append(f"Expected sha256(`{output_path}`) = `{output_hash}`")
        lines.append("")
    lines.append("## How to run the bundled tests")
    lines.append("")
    lines.append("```bash")
    lines.append("# from the unpacked archive root")
    lines.append("python3 -m pytest generated/tests/ -q")
    lines.append("```")
    lines.append("")
    lines.append("## Validation layers")
    lines.append("")
    lines.append(f"Overall: **{overall.upper()}**")
    lines.append("")
    if validation_summary:
        lines.append("| Layer | Status |")
        lines.append("|---|---|")
        for entry in validation_summary:
            layer = entry.get("layer", "")
            status = entry.get("status", "")
            lines.append(f"| `{layer}` | **{status.upper()}** |")
        lines.append("")
    if report_hash:
        lines.append(
            f"System validation report: `{report_path}` (sha256=`{report_hash}`). "
            "JSON sidecar lives next to the markdown."
        )
        workflow_report = completion.get("workflow_report_path")
        if workflow_report:
            lines.append(f"Workflow report deliverable: `{workflow_report}`")
        lines.append("")
    _append_artifact_and_audit_sections(lines, artifacts, completion=completion)
    (workspace / "SESSION_README.md").write_text("\n".join(lines), encoding="utf-8")


def _write_custom_workflow_session_readme(
    workspace: Path,
    manifest: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    workflow_type = completion.get("workflow_type") or "custom_finance_workflow"
    provider = completion.get("llm_provider") or "(unknown)"
    model = completion.get("llm_model") or "(unknown)"
    base_url = completion.get("llm_base_url") or ""
    model_calls = completion.get("model_call_count", 0)
    tokens_used = completion.get("tokens_used", 0)
    model_files = completion.get("model_contributed_files") or []
    provenance_artifacts = completion.get("authoring_provenance_artifacts") or []
    tests_passed = completion.get("tests_passed")
    validation_passed = completion.get("validation_passed")
    via = completion.get("completion_via") or manifest.get("status", "unknown")
    overall = completion.get("validation_overall") or "unknown"
    input_file = completion.get("input_file") or "uploads/"
    input_format = completion.get("input_format") or "csv"
    selected_sheet = completion.get("selected_sheet")
    normalized_input = completion.get("normalized_input_path")
    output_path = completion.get("output_path") or "outputs/output.csv"
    output_hash = completion.get("output_hash") or ""
    report_path = completion.get("validation_report_path") or "reports/system_validation_report.md"
    report_hash = completion.get("validation_report_hash") or ""
    tests_path = completion.get("tests_path") or "generated/tests"
    skipped_layers = completion.get("skipped_layers") or []
    warnings = completion.get("warnings") or []
    artifacts = completion.get("artifacts") or []
    validation_summary = completion.get("validation_summary") or []
    budget = manifest.get("budget") or {}
    budget_display = effective_budget_display(completion, budget)
    session_id = manifest.get("session_id", "")
    workflow = manifest.get("workflow", "author")
    started_at = manifest.get("started_at", "")
    updated_at = manifest.get("updated_at", "")

    agent_input = normalized_input or input_file
    lines: list[str] = []
    lines.append(f"# AgentForge session — {session_id}")
    lines.append("")
    lines.append(
        f"Workflow: **{workflow}** · Status: **{manifest.get('status', 'unknown')}** · "
        f"Started: {started_at} · Updated: {updated_at}"
    )
    lines.append("")
    lines.append("## How this was built")
    lines.append("")
    lines.append("- Build path: **AI-authored workflow build**")
    lines.append(
        f"- Workflow type: **{workflow_type.replace('_', ' ')}**"
    )
    lines.append(f"- Completion path: `{via}`")
    lines.append(f"- Build mode: `{completion.get('build_mode', 'llm_authoring')}`")
    failure = completion.get("failure") or {}
    failed_check = failure.get("failed_check") or completion.get("failed_check")
    if failed_check:
        lines.append(f"- Failed check: `{failed_check}`")
    failed_layer = failure.get("failed_layer")
    if failed_layer:
        lines.append(f"- Failed layer: `{failed_layer}`")
    pytest_summary = failure.get("pytest_summary") or completion.get("pytest_summary")
    if pytest_summary:
        lines.append(f"- Pytest summary: `{pytest_summary}`")
    lines.append("")
    lines.append("## Input files")
    lines.append("")
    lines.append(f"- Original upload: `{input_file}` ({input_format})")
    if selected_sheet:
        lines.append(f"- Selected sheet: `{selected_sheet}`")
    if normalized_input:
        lines.append(f"- Normalised working CSV: `{normalized_input}`")
    _append_date_clarification_readme_section(lines, workspace)
    lines.append("")
    lines.append("## LLM provider")
    lines.append("")
    lines.append(f"- Provider: `{provider}`")
    lines.append(f"- Model: `{model}`")
    if base_url:
        lines.append(f"- Base URL: `{base_url}`")
    lines.append(f"- Model calls (across pause+resume): **{model_calls}**")
    lines.append(f"- Tokens used: **{tokens_used}**")
    if model_files:
        lines.append("- Model-authored files:")
        for path in model_files:
            lines.append(f"  - `{path}`")
    if provenance_artifacts:
        lines.append("- Provenance artifacts:")
        for path in provenance_artifacts:
            lines.append(f"  - `{path}`")
    lines.append(f"- Generated tests passed: **{'yes' if tests_passed else 'no'}**")
    lines.append(f"- Validation passed: **{'yes' if validation_passed else 'no'}**")
    lines.append(
        f"- Budget on completion: tokens={budget_display['tokens_used']} / "
        f"tool_calls={budget_display['tool_calls_used']} / "
        f"steps={budget_display['steps_used']} / "
        f"wall_seconds={budget_display['wall_seconds_used']}"
    )
    lines.append("")
    lines.append(f"## How to reproduce `{output_path}`")
    lines.append("")
    workflow_report = completion.get("workflow_report_path") or "reports/validation_report.md"
    lines.append("```bash")
    lines.append("# from the unpacked archive root")
    lines.append(
        f"python3 generated/agent.py --input {agent_input} "
        f"--contract generated/author_output_contract.json "
        f"--row-output {output_path} --report-path {workflow_report}"
    )
    lines.append("```")
    lines.append("")
    if output_hash:
        lines.append(f"Expected sha256(`{output_path}`) = `{output_hash}`")
        lines.append("")
    lines.append("## How to run the bundled tests")
    lines.append("")
    lines.append("```bash")
    lines.append("# from the unpacked archive root")
    lines.append(f"python3 -m pytest {tests_path}/ -q")
    lines.append("```")
    lines.append("")
    lines.append("## Validation layers")
    lines.append("")
    lines.append(f"Overall: **{overall.upper()}**")
    lines.append("")
    if validation_summary:
        lines.append("| Layer | Status |")
        lines.append("|---|---|")
        for entry in validation_summary:
            layer = entry.get("layer", "")
            status = entry.get("status", "")
            lines.append(f"| `{layer}` | **{status.upper()}** |")
        lines.append("")
    if skipped_layers:
        lines.append("Skipped checks:")
        for layer in skipped_layers:
            lines.append(f"- `{layer}`")
        lines.append("")
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    if report_hash:
        lines.append(
            f"System validation report: `{report_path}` (sha256=`{report_hash}`). "
            "JSON sidecar lives next to the markdown."
        )
        workflow_report = completion.get("workflow_report_path")
        if workflow_report:
            lines.append(f"Workflow report deliverable: `{workflow_report}`")
        lines.append("")
    _append_artifact_and_audit_sections(lines, artifacts, completion=completion)
    (workspace / "SESSION_README.md").write_text("\n".join(lines), encoding="utf-8")


def _write_repair_session_readme(
    workspace: Path,
    manifest: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    provider = completion.get("llm_provider") or "(unknown)"
    model = completion.get("llm_model") or "(unknown)"
    base_url = completion.get("llm_base_url") or ""
    model_calls = completion.get("model_call_count", 0)
    via = completion.get("completion_via") or manifest.get("status", "unknown")
    artifacts = completion.get("artifacts") or []
    budget = manifest.get("budget") or {}
    budget_display = effective_budget_display(completion, budget)
    session_id = manifest.get("session_id", "")
    workflow = manifest.get("workflow", "repair")
    started_at = manifest.get("started_at", "")
    updated_at = manifest.get("updated_at", "")
    changed_files = completion.get("changed_files") or []
    before_summary = completion.get("before_fix_pytest_summary") or "not run"
    after_summary = completion.get("after_fix_pytest_summary") or "not run"
    repair_report_path = completion.get("repair_report_path") or "reports/repair_report.md"
    repair_report_json_path = (
        completion.get("repair_report_json_path") or "reports/repair_report.json"
    )
    patch_diff_path = completion.get("patch_diff_path") or "reports/agent_py.patch"
    before_log_path = (
        completion.get("before_fix_log_path") or "reports/before_fix_pytest_output.txt"
    )
    after_log_path = (
        completion.get("after_fix_log_path") or "reports/after_fix_pytest_output.txt"
    )
    repair_dir = _repair_working_dir(workspace, changed_files)

    lines: list[str] = []
    lines.append(f"# AgentForge session — {session_id}")
    lines.append("")
    lines.append(
        f"Workflow: **{workflow}** · Status: **{manifest.get('status', 'unknown')}** · "
        f"Started: {started_at} · Updated: {updated_at}"
    )
    lines.append("")
    lines.append("## How this was repaired")
    lines.append("")
    lines.append("- Workflow: **repair**")
    lines.append(f"- Completion path: `{via}`")
    lines.append(
        f"- Failure reproduced: **{'yes' if completion.get('repair_reproduced') else 'no'}**"
    )
    lines.append(
        f"- Patch applied: **{'yes' if completion.get('patch_applied') else 'no'}**"
    )
    lines.append(
        "- Post-fix tests passed: "
        f"**{'yes' if completion.get('post_fix_tests_passed') else 'no'}**"
    )
    if changed_files:
        lines.append("- Changed files:")
        for path in changed_files:
            lines.append(f"  - `{path}`")
    lines.append(f"- Pytest summary before fix: `{before_summary}`")
    lines.append(f"- Pytest summary after fix: `{after_summary}`")
    lines.append(f"- Repair report: `{repair_report_path}`")
    lines.append(f"- Repair report (JSON): `{repair_report_json_path}`")
    lines.append(f"- Patch diff: `{patch_diff_path}`")
    lines.append(f"- Before-fix pytest log: `{before_log_path}`")
    lines.append(f"- After-fix pytest log: `{after_log_path}`")
    lines.append("")
    lines.append("## LLM provider")
    lines.append("")
    lines.append(f"- Provider: `{provider}`")
    lines.append(f"- Model: `{model}`")
    if base_url:
        lines.append(f"- Base URL: `{base_url}`")
    lines.append(f"- Model calls (across pause+resume): **{model_calls}**")
    lines.append(
        f"- Budget on completion: tokens={budget_display['tokens_used']} / "
        f"tool_calls={budget_display['tool_calls_used']} / "
        f"steps={budget_display['steps_used']} / "
        f"wall_seconds={budget_display['wall_seconds_used']}"
    )
    lines.append("")
    lines.append("## How to run the repaired agent tests")
    lines.append("")
    lines.append("```bash")
    lines.append("# from the unpacked archive root")
    lines.append(f"cd {repair_dir}")
    lines.append("python3 -m pytest tests/ -q")
    lines.append("```")
    lines.append("")
    _append_artifact_and_audit_sections(lines, artifacts, completion=completion)
    (workspace / "SESSION_README.md").write_text("\n".join(lines), encoding="utf-8")


def _repair_working_dir(workspace: Path, changed_files: list[Any]) -> str:
    """Resolve the repaired agent directory for reproduction instructions."""
    for entry in changed_files:
        rel = str(entry)
        parent = Path(rel).parent.as_posix()
        if parent and parent != ".":
            candidate = workspace / parent
            if candidate.is_dir() and (candidate / "tests").is_dir():
                return parent
    working = workspace / "working"
    if working.is_dir():
        for agent in sorted(working.rglob("agent.py")):
            if (agent.parent / "tests").is_dir():
                return str(agent.parent.relative_to(workspace)).replace("\\", "/")
    return "working/<agent_package>"


def _append_date_clarification_readme_section(
    lines: list[str],
    workspace: Path,
) -> None:
    events_path = workspace / "events.jsonl"
    if not events_path.is_file():
        return
    clarification: dict[str, Any] | None = None
    for raw_line in events_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        if payload.get("kind") != "clarification_answered":
            continue
        clarification = payload
    if clarification is None:
        return
    date_format = clarification.get("date_format")
    affected = clarification.get("affected_columns") or []
    raw_answer = clarification.get("raw_answer") or ""
    sample_values = clarification.get("sample_values") or {}
    lines.append("## Date format clarification")
    lines.append("")
    lines.append(f"- Parsed format: **{date_format}**")
    if affected:
        lines.append(f"- Affected columns: {', '.join(f'`{col}`' for col in affected)}")
    if raw_answer:
        lines.append(f"- User answer: \"{raw_answer}\"")
    if isinstance(sample_values, dict) and sample_values:
        lines.append("- Sample values reviewed:")
        for column, values in sample_values.items():
            if isinstance(values, list):
                joined = ", ".join(str(value) for value in values)
                lines.append(f"  - `{column}`: {joined}")


def _append_artifact_and_audit_sections(
    lines: list[str],
    artifacts: list[Any],
    *,
    completion: dict[str, Any] | None = None,
) -> None:
    workflow_types = {
        "workflow_row_output",
        "workflow_report",
        "output_csv",
        "summary_csv",
        "exceptions_csv",
        "output_file",
    }
    system_types = {
        "system_validation_report",
        "validation_report",
    }
    workflow_deliverables: list[Any] = []
    system_audit: list[Any] = []
    for artifact in artifacts:
        atype = str(artifact.get("artifact_type") or "")
        if atype in workflow_types:
            workflow_deliverables.append(artifact)
        elif atype in system_types:
            system_audit.append(artifact)
        else:
            system_audit.append(artifact)

    completion = completion or {}
    output_path = completion.get("output_path")
    workflow_report_path = completion.get("workflow_report_path")
    system_report_path = completion.get("validation_report_path")

    lines.append("## Workflow deliverables")
    lines.append("")
    if workflow_deliverables:
        lines.append("| Type | Path | Size | sha256 |")
        lines.append("|---|---|---|---|")
        for artifact in workflow_deliverables:
            lines.append(
                f"| `{artifact.get('artifact_type', '')}` | `{artifact.get('path', '')}` | "
                f"{artifact.get('size_bytes') or ''} | "
                f"`{(artifact.get('hash_sha256') or '')[:12]}…` |"
            )
        lines.append("")
    else:
        if output_path:
            lines.append(f"- Row-level output: `{output_path}`")
        if workflow_report_path:
            lines.append(f"- Workflow report: `{workflow_report_path}`")
        if not output_path and not workflow_report_path:
            lines.append("- (none recorded)")
        lines.append("")

    lines.append("## System audit files")
    lines.append("")
    if system_audit:
        lines.append("| Type | Path | Size | sha256 |")
        lines.append("|---|---|---|---|")
        for artifact in system_audit:
            lines.append(
                f"| `{artifact.get('artifact_type', '')}` | `{artifact.get('path', '')}` | "
                f"{artifact.get('size_bytes') or ''} | "
                f"`{(artifact.get('hash_sha256') or '')[:12]}…` |"
            )
        lines.append("")
    elif system_report_path:
        lines.append(f"- System validation report: `{system_report_path}`")
        lines.append("")
    lines.append("## Audit trail")
    lines.append("")
    lines.append(
        "- `events.jsonl` — append-only event log (one JSON per line). Every model call, "
        "every tool call, every validation step is here."
    )
    lines.append(
        "- `manifest.json` — terminal-state snapshot, including this completion metadata."
    )
    lines.append("")
    lines.append(
        "**Synthetic data only.** Do not feed real client names, banking details, or "
        "tax IDs into this pipeline."
    )
    lines.append("")


__all__ = ["ARCHIVE_FILENAME", "ArchiveResult", "build_archive"]
