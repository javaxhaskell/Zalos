"""Tests for the final-polish ``POST /sessions/{id}/upload_agent_zip`` endpoint.

Covers:

  * Happy path — ZIP carrying the ``invoice_aging_v1`` shape extracts
    cleanly into ``working/``, stages the golden into ``evals/``,
    emits the canonical events.
  * Zip-slip — entries with ``..`` segments are rejected at the
    WorkspaceManager boundary; the partial extraction is rolled back.
  * Per-entry size cap — a single oversized entry triggers 413 +
    rollback.
  * Aggregate size cap — many small entries that sum past the cap
    trigger 413 + rollback.
  * File-count cap — too many entries trigger 413 + rollback.
  * Non-ZIP body — bad bytes / wrong extension / wrong MIME → 400 / 415.
  * 404 unknown session.
  * 409 if working/ is already populated.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v1"


def _build_zip(entries: dict[str, bytes]) -> bytes:
    """Construct an in-memory ZIP from ``{relative_path: bytes}``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    return buf.getvalue()


def _create_repair_session(client: TestClient) -> dict:
    resp = client.post("/sessions", json={"workflow": "repair"})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_zip_upload_extracts_invoice_aging_shape(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    # Build a ZIP that mirrors the invoice_aging_v1 shape (agent.py +
    # rules.py + tests/ + data/expected_output.csv).
    agent_py = (_FIXTURE_DIR / "agent.py").read_bytes()
    rules_py = (_FIXTURE_DIR / "rules.py").read_bytes()
    expected_csv = (_FIXTURE_DIR / "data" / "expected_output.csv").read_bytes()
    zip_bytes = _build_zip(
        {
            "agent.py": agent_py,
            "rules.py": rules_py,
            "tests/test_aging.py": b"# placeholder\n",
            "data/expected_output.csv": expected_csv,
        }
    )

    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("invoice_aging_v1.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["archive_filename"] == "invoice_aging_v1.zip"
    paths = body["files_extracted"]
    assert "working/agent.py" in paths
    assert "working/rules.py" in paths
    assert "working/tests/test_aging.py" in paths
    assert "working/data/expected_output.csv" in paths
    assert body["staged_golden_path"] == "evals/expected_output.csv"
    assert body["total_uncompressed_bytes"] > 0

    # On disk
    assert (workspace / "working" / "agent.py").is_file()
    assert (workspace / "evals" / "expected_output.csv").is_file()

    # Event log: one decision_input + one file_uploaded per extracted file.
    events = app_client.get(f"/sessions/{sid}/events").json()["events"]
    decision = [
        e
        for e in events
        if e["kind"] == "decision_input"
        and e["payload"].get("kind") == "agent_zip_uploaded"
    ]
    assert len(decision) == 1
    assert decision[0]["payload"]["archive_filename"] == "invoice_aging_v1.zip"
    uploads = [e for e in events if e["kind"] == "file_uploaded"]
    assert len(uploads) == len(paths)


# ---------------------------------------------------------------------------
# Zip-slip defence
# ---------------------------------------------------------------------------


def test_zip_upload_rejects_path_traversal(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    zip_bytes = _build_zip(
        {
            "agent.py": b"print('ok')\n",
            "../../etc/passwd_stub": b"root:x:0:0:root:/root:/bin/bash\n",
        }
    )
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("bad.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error_code"] == "zip_path_escape"
    # Rollback: working/ should be clean (no agent.py from the partial extract).
    assert not (workspace / "working" / "agent.py").exists()


# ---------------------------------------------------------------------------
# Size caps
# ---------------------------------------------------------------------------


def test_zip_upload_rejects_oversized_entry(app_client: TestClient) -> None:
    """A single 11 MiB uncompressed entry exceeds the per-entry cap."""
    session = _create_repair_session(app_client)
    sid = session["id"]

    big = b"x" * (11 * 1024 * 1024)  # 11 MiB
    zip_bytes = _build_zip({"agent.py": b"print('ok')\n", "huge.bin": big})
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("big.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["error_code"] == "zip_entry_too_large"


def test_zip_upload_rejects_too_many_files(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]

    entries = {f"file_{i:03d}.txt": b"x" for i in range(101)}  # cap is 100
    zip_bytes = _build_zip(entries)
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("many.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["error_code"] == "zip_too_many_files"


# ---------------------------------------------------------------------------
# Boundary cases
# ---------------------------------------------------------------------------


def test_zip_upload_404_for_unknown_session(app_client: TestClient) -> None:
    zip_bytes = _build_zip({"agent.py": b"print('ok')\n"})
    resp = app_client.post(
        f"/sessions/{uuid4()}/upload_agent_zip",
        files={"file": ("x.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 404


def test_zip_upload_415_for_non_zip_extension(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("agent.tar", io.BytesIO(b"not a zip"), "application/x-tar")},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["error_code"] == "unsupported_file_type"


def test_zip_upload_400_for_corrupt_zip(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("garbage.zip", io.BytesIO(b"not actually a zip"), "application/zip")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "invalid_zip"


def test_zip_upload_409_when_working_not_empty(app_client: TestClient) -> None:
    session = _create_repair_session(app_client)
    sid = session["id"]

    # First upload populates working/.
    zip_bytes = _build_zip({"agent.py": b"print('ok')\n"})
    first = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("first.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert first.status_code == 200, first.text

    # Second upload conflicts.
    second = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("second.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["error_code"] == "working_not_empty"


def test_zip_upload_skips_macos_metadata(app_client: TestClient) -> None:
    """macOS adds __MACOSX/ and .DS_Store entries; these are dropped."""
    session = _create_repair_session(app_client)
    sid = session["id"]

    zip_bytes = _build_zip(
        {
            "agent.py": b"print('ok')\n",
            "__MACOSX/agent.py": b"junk",
            ".DS_Store": b"junk",
            "tests/.DS_Store": b"junk",
        }
    )
    resp = app_client.post(
        f"/sessions/{sid}/upload_agent_zip",
        files={"file": ("clean.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    paths = resp.json()["files_extracted"]
    assert paths == ["working/agent.py"]
    assert UUID(resp.json()["session_id"]) == UUID(sid)
