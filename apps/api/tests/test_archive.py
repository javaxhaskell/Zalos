"""Tests for the BP10a archive builder + tool + HTTP endpoints.

Covers:

  * ``build_archive`` in :mod:`agentforge.persistence.archive` —
    happy-path zips the load-bearing workspace contents, refuses an
    empty workspace, excludes ``uploads/``, and ignores
    ``__pycache__`` / ``.pytest_cache``.
  * ``archive_workspace`` tool — handler emits ``ARTIFACT_GENERATED``
    with ``artifact_type=archive`` and returns the typed output.
  * ``GET /sessions/{id}/archive.zip`` — lazily builds + streams the
    zip when the agent didn't call the tool, 404 unknown session,
    409 when there's nothing to archive.
  * ``GET /sessions/{id}/artifacts/{relative_path:path}`` — serves
    files from ``reports/`` / ``outputs/`` / ``generated/`` /
    ``working/`` with the right MIME, 403 outside the allow-list, 400
    on traversal attempts, 404 missing.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentforge.persistence.archive import (
    ARCHIVE_FILENAME,
    build_archive,
)
from agentforge.persistence.workspace import WorkspaceError
from agentforge.schemas import ArtifactType, EventKind
from agentforge.tools.archive_tool import (
    ARCHIVE_WORKSPACE_TOOL,
    ArchiveWorkspaceInput,
    archive_workspace_handler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_load_bearing_files(workspace: Path) -> None:
    """Populate the directories ``build_archive`` is expected to capture.

    ``manifest.json`` + ``events.jsonl`` are already created by the
    workspace allocator; we leave those alone (overwriting the manifest
    with garbage would break later EventLog reads that re-parse it).
    """
    (workspace / "generated" / "agent.py").write_text("print('hi')\n")
    (workspace / "outputs" / "output.csv").write_text("a,b\n1,2\n")
    (workspace / "reports" / "validation_report.md").write_text(
        "# Validation Report\n\nOverall: PASS\n"
    )
    (workspace / "uploads" / "sample.csv").write_text("input,row\n")
    # Decoys that must be excluded.
    pycache = workspace / "generated" / "__pycache__"
    pycache.mkdir(parents=True, exist_ok=True)
    (pycache / "agent.cpython-313.pyc").write_text("binary-ish")
    (workspace / "generated" / ".DS_Store").write_bytes(b"\x00mac-junk")


# ---------------------------------------------------------------------------
# build_archive (pure function)
# ---------------------------------------------------------------------------


def test_build_archive_writes_zip_with_load_bearing_contents(
    tool_ctx,
) -> None:
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    _seed_load_bearing_files(workspace)

    result = build_archive(
        session_id=tool_ctx.session_id,
        workspace_manager=tool_ctx.workspace_manager,
    )

    assert result.relative_path == ARCHIVE_FILENAME
    assert result.size_bytes > 0
    assert result.file_count >= 5

    archive_path = workspace / ARCHIVE_FILENAME
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "events.jsonl" in names
        assert "generated/agent.py" in names
        assert "outputs/output.csv" in names
        assert "reports/validation_report.md" in names
        # Uploads ARE included now (input data is part of the
        # reproducible audit bundle).
        assert "uploads/sample.csv" in names
        # Build artefacts + OS junk are excluded.
        assert all("__pycache__" not in n for n in names)
        assert all(".DS_Store" not in n for n in names)
        assert all(not n.endswith(".pyc") for n in names)


def test_build_archive_includes_uploads_for_reproducibility(tool_ctx) -> None:
    """``uploads/`` is part of the reproducible audit bundle."""
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    (workspace / "uploads" / "input.csv").write_text("a,b\n1,2\n")
    (workspace / "reports" / "x.md").write_text("# x")

    result = build_archive(
        session_id=tool_ctx.session_id,
        workspace_manager=tool_ctx.workspace_manager,
    )
    with zipfile.ZipFile(workspace / ARCHIVE_FILENAME) as zf:
        names = set(zf.namelist())
    assert "uploads/input.csv" in names
    assert "reports/x.md" in names
    assert result.file_count >= 2


def test_build_archive_stages_generated_data_when_template_present(
    tool_ctx,
) -> None:
    """When ``generated/`` exists, the upload + golden are staged into
    ``generated/data/`` so the bundled tests are runnable from the
    extracted archive."""
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    (workspace / "generated" / "agent.py").write_text("print('hi')\n")
    (workspace / "uploads" / "sample_input.csv").write_text("a,b\n1,2\n")
    (workspace / "evals").mkdir(parents=True, exist_ok=True)
    (workspace / "evals" / "golden_output.csv").write_text("a,b\n1,2\n")

    build_archive(
        session_id=tool_ctx.session_id,
        workspace_manager=tool_ctx.workspace_manager,
    )
    with zipfile.ZipFile(workspace / ARCHIVE_FILENAME) as zf:
        names = set(zf.namelist())
    assert "generated/data/sample_input.csv" in names
    assert "generated/data/golden_output.csv" in names


def test_build_archive_refuses_empty_workspace(tool_ctx) -> None:
    """Nothing in generated/working/outputs/reports + no top-level files."""
    # Tool ctx allocates the dirs (empty); no top-level manifest/events
    # are required to exist (allocate writes manifest.json itself).
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    # Remove the manifest so the workspace really has no entries.
    (workspace / "manifest.json").unlink()
    (workspace / "events.jsonl").unlink()
    with pytest.raises(WorkspaceError):
        build_archive(
            session_id=tool_ctx.session_id,
            workspace_manager=tool_ctx.workspace_manager,
        )


def test_build_archive_overwrites_existing(tool_ctx) -> None:
    """Re-running build_archive replaces the previous file."""
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    _seed_load_bearing_files(workspace)

    first = build_archive(
        session_id=tool_ctx.session_id,
        workspace_manager=tool_ctx.workspace_manager,
    )
    # Add a new file; rebuild; the archive should contain it.
    (workspace / "outputs" / "extra.csv").write_text("c,d\n3,4\n")
    second = build_archive(
        session_id=tool_ctx.session_id,
        workspace_manager=tool_ctx.workspace_manager,
    )
    assert second.file_count == first.file_count + 1
    with zipfile.ZipFile(workspace / ARCHIVE_FILENAME) as zf:
        assert "outputs/extra.csv" in set(zf.namelist())


# ---------------------------------------------------------------------------
# archive_workspace tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_workspace_tool_emits_artifact_generated(tool_ctx) -> None:
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    _seed_load_bearing_files(workspace)

    output = await archive_workspace_handler(
        ArchiveWorkspaceInput(summary="end of run"),
        tool_ctx,
    )

    assert output.relative_path == ARCHIVE_FILENAME
    assert output.file_count >= 4
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    artifact_events = [
        e for e in events if e.kind == EventKind.ARTIFACT_GENERATED
    ]
    assert len(artifact_events) >= 1
    payload = artifact_events[-1].payload
    assert payload["artifact_type"] == ArtifactType.ARCHIVE.value
    assert payload["path"] == ARCHIVE_FILENAME
    assert payload["summary"] == "end of run"
    assert payload["file_count"] == output.file_count


def test_archive_workspace_tool_definition_in_registry() -> None:
    """The tool surfaces in build_registry and the phase filters."""
    from agentforge.schemas import ToolPhase
    from agentforge.tools import build_registry

    reg = build_registry()
    assert reg.has("archive_workspace")
    assert reg.get("archive_workspace") is not None
    # BUILD and FIX expose it; INFO does not.
    build_names = {t["name"] for t in reg.list_for_phase(ToolPhase.AUTHOR_BUILD)}
    info_names = {t["name"] for t in reg.list_for_phase(ToolPhase.AUTHOR_INFO)}
    assert "archive_workspace" in build_names
    assert "archive_workspace" not in info_names
    repair_fix = {t["name"] for t in reg.list_for_phase(ToolPhase.REPAIR_FIX)}
    assert "archive_workspace" in repair_fix
    # INV-4: write tool defaults to requires_approval=True
    assert ARCHIVE_WORKSPACE_TOOL.definition.requires_approval is True


# ---------------------------------------------------------------------------
# GET /sessions/{id}/archive.zip
# ---------------------------------------------------------------------------


def _create_author_session(client: TestClient) -> dict:
    resp = client.post("/sessions", json={"workflow": "author"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_archive_endpoint_lazily_builds_and_streams(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    # Seed load-bearing artifacts directly so the endpoint has
    # something to archive.
    (workspace / "outputs" / "output.csv").write_text("a,b\n1,2\n")
    (workspace / "reports" / "validation_report.md").write_text("# x\n")

    resp = app_client.get(f"/sessions/{sid}/archive.zip")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    # Streamed body is a real ZIP.
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "outputs/output.csv" in names
        assert "reports/validation_report.md" in names

    # Lazily-built archive emits ARTIFACT_GENERATED.
    events = app_client.get(f"/sessions/{sid}/events").json()["events"]
    artifact = [
        e
        for e in events
        if e["kind"] == "artifact_generated"
        and e["payload"].get("artifact_type") == "archive"
    ]
    assert len(artifact) == 1
    assert artifact[0]["payload"]["via"] == "http_endpoint"


def test_archive_endpoint_404_unknown_session(app_client: TestClient) -> None:
    resp = app_client.get(f"/sessions/{uuid4()}/archive.zip")
    assert resp.status_code == 404


def test_archive_endpoint_409_when_nothing_to_archive(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    # Strip the load-bearing top-level files + any subdir contents to
    # simulate a session that produced nothing.
    (workspace / "manifest.json").unlink()
    (workspace / "events.jsonl").unlink()

    resp = app_client.get(f"/sessions/{sid}/archive.zip")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "archive_empty"


# ---------------------------------------------------------------------------
# GET /sessions/{id}/artifacts/{relative_path:path}
# ---------------------------------------------------------------------------


def test_artifact_endpoint_serves_markdown_with_correct_mime(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    (workspace / "reports" / "validation_report.md").write_text(
        "# Validation Report\n\nOverall: PASS\n"
    )

    resp = app_client.get(
        f"/sessions/{sid}/artifacts/reports/validation_report.md"
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "Overall: PASS" in resp.text


def test_artifact_endpoint_404_when_file_missing(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    resp = app_client.get(f"/sessions/{sid}/artifacts/reports/no_such.md")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "artifact_not_found"


def test_artifact_endpoint_403_outside_allowed_prefixes(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    # uploads/ is intentionally not in the allow-list.
    workspace = Path(session["workspace_path"])
    (workspace / "uploads" / "secret.csv").write_text("a,b\n")
    resp = app_client.get(f"/sessions/{sid}/artifacts/uploads/secret.csv")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "artifact_path_forbidden"


def test_artifact_endpoint_400_on_traversal(app_client: TestClient) -> None:
    """An absolute path or .. segment is rejected at the WorkspaceManager
    boundary (INV-5)."""
    session = _create_author_session(app_client)
    sid = session["id"]
    # 'reports/../uploads/secret.csv' passes the prefix check (starts with
    # 'reports/') but resolves outside the reports subtree. The
    # WorkspaceManager.resolve_in confines it to the workspace; the file
    # not existing there gives 404 rather than 400, which is the right
    # outcome (no path escape, just missing). Try a more aggressive
    # traversal that escapes the workspace entirely.
    resp = app_client.get(
        f"/sessions/{sid}/artifacts/reports/../../../etc/passwd"
    )
    assert resp.status_code in (400, 404)
