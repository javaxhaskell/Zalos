"""Session lifecycle integration test (Build Prompt 3).

Exercises the end-to-end happy path that subsequent prompts build on:

  1. Create session via POST /sessions.
  2. Workspace dir is allocated with the canonical layout.
  3. events.jsonl contains the load-bearing initial events.
  4. Manifest matches the DB row.
  5. Upload a CSV via POST /sessions/{id}/files.
  6. File is persisted under uploads/, hashed, and the FILE_UPLOADED event
     appears in the log + the manifest's file_hashes.
  7. GET /sessions/{id} returns the session with updated file_count.
  8. GET /sessions/{id}/events returns the full event log, polling-friendly.
  9. GET /audit/export/{id} returns the chain + manifest with chain_check.valid==True.
 10. Resume simulation: SessionStore.resume_check reports no drift.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_author_session(client: TestClient) -> dict:
    response = client.post("/sessions", json={"workflow": "author"})
    assert response.status_code == 201, response.text
    return response.json()


def _read_events_jsonl(workspace: Path) -> list[dict]:
    path = workspace / "events.jsonl"
    assert path.is_file(), f"missing {path}"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Lifecycle smoke
# ---------------------------------------------------------------------------


def test_create_session_allocates_workspace_with_initial_events(
    app_client: TestClient,
) -> None:
    body = _create_author_session(app_client)

    # Status + identifiers
    assert body["workflow"] == "author"
    assert body["status"] == "created"
    sid = body["id"]
    workspace = Path(body["workspace_path"])

    # Workspace layout
    assert workspace.is_dir()
    assert (workspace / "manifest.json").is_file()
    assert (workspace / "events.jsonl").is_file()
    for sub in ("uploads", "generated", "working", "outputs", "outputs/_logs", "reports"):
        assert (workspace / sub).is_dir(), f"missing {sub}"

    # Manifest mirror matches the DB row
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["session_id"] == sid
    assert manifest["workflow"] == "author"
    assert manifest["status"] == "created"
    assert manifest["workspace_path"] == str(workspace)
    assert manifest["budget"]["tokens_limit"] == 150_000

    # Initial events
    events = _read_events_jsonl(workspace)
    kinds = [e["kind"] for e in events]
    assert "workflow_started" in kinds
    assert "workspace_allocated" in kinds
    # Chain integrity: first event has prev_event_id == None; subsequent
    # events chain to the previous id.
    assert events[0]["prev_event_id"] is None
    for prev, curr in zip(events, events[1:], strict=False):
        assert curr["prev_event_id"] == prev["id"], (
            f"chain break: {curr['id']} prev was {curr['prev_event_id']}, expected {prev['id']}"
        )


def test_get_and_list_sessions(app_client: TestClient) -> None:
    a = _create_author_session(app_client)
    b = _create_author_session(app_client)
    assert a["id"] != b["id"]

    # GET single
    single = app_client.get(f"/sessions/{a['id']}")
    assert single.status_code == 200
    assert single.json()["id"] == a["id"]

    # GET list (newest first)
    listing = app_client.get("/sessions")
    assert listing.status_code == 200
    ids = [s["id"] for s in listing.json()["sessions"]]
    assert a["id"] in ids and b["id"] in ids

    # Missing session → 404 with structured envelope
    missing = app_client.get("/sessions/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "unknown"  # default code for plain APIError


def test_upload_persists_file_emits_event_and_updates_manifest(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    csv_payload = b"txn_id,date,amount,description,counterparty,account\n" \
                  b"T-001,2026-01-01,100.00,COFFEE SHOP,Costa,ACC-001\n" \
                  b"T-002,2026-01-02,250.00,UBER TRIP,Uber,ACC-001\n"

    upload = app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("transactions.csv", io.BytesIO(csv_payload), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["filename"] == "transactions.csv"
    assert body["size_bytes"] == len(csv_payload)
    assert body["relative_path"] == "uploads/transactions.csv"

    # File on disk
    on_disk = workspace / "uploads" / "transactions.csv"
    assert on_disk.is_file()
    assert on_disk.read_bytes() == csv_payload

    # Manifest updated with the hash
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["file_hashes"]["uploads/transactions.csv"] == body["hash_sha256"]

    # FILE_UPLOADED event appended
    events = _read_events_jsonl(workspace)
    file_events = [e for e in events if e["kind"] == "file_uploaded"]
    assert len(file_events) == 1
    assert file_events[0]["payload"]["filename"] == "transactions.csv"

    # Session row's file_count incremented
    refreshed = app_client.get(f"/sessions/{sid}").json()
    assert refreshed["budget"]["file_count"] == 1


def test_upload_rejects_unsupported_extension(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]

    resp = app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 415
    assert resp.json()["error_code"] == "unsupported_file_type"


def test_upload_rejects_oversized_file(
    app_client: TestClient, monkeypatch
) -> None:
    """A file larger than BUDGET_FILE_UPLOAD_MAX_BYTES is rejected."""
    # Temporarily shrink the per-file budget for the test.
    monkeypatch.setenv("BUDGET_FILE_UPLOAD_MAX_BYTES", "128")
    # Force a fresh Settings reload.
    import agentforge.config as config_module

    config_module._settings = None

    session = _create_author_session(app_client)
    sid = session["id"]
    oversized = b"x" * 256

    resp = app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("big.csv", io.BytesIO(oversized), "text/csv")},
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "file_too_large"


def test_events_endpoint_supports_polling_with_after(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]

    initial = app_client.get(f"/sessions/{sid}/events")
    assert initial.status_code == 200
    initial_body = initial.json()
    assert initial_body["session_id"] == sid
    assert len(initial_body["events"]) >= 2

    # ?after=<first_id> returns strictly subsequent events
    first_id = initial_body["events"][0]["id"]
    after_response = app_client.get(
        f"/sessions/{sid}/events", params={"after": first_id}
    )
    assert after_response.status_code == 200
    after_body = after_response.json()
    assert len(after_body["events"]) == len(initial_body["events"]) - 1
    assert all(e["id"] != first_id for e in after_body["events"])

    # ?after=<last_id> returns empty (frontend polling steady state)
    last_id = initial_body["events"][-1]["id"]
    none_response = app_client.get(
        f"/sessions/{sid}/events", params={"after": last_id}
    )
    assert none_response.status_code == 200
    assert none_response.json()["events"] == []


def test_audit_export_includes_chain_and_manifest(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    # Upload a file so the export has substance.
    app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )

    resp = app_client.get(f"/audit/export/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert body["event_count"] == len(body["events"]) >= 3
    assert body["chain_check"]["valid"] is True
    assert body["manifest"]["session_id"] == sid
    assert "uploads/x.csv" in body["manifest"]["file_hashes"]


def test_resume_check_reports_no_drift_on_clean_session(
    app_client: TestClient,
) -> None:
    """SessionStore.resume_check returns zero warnings on an untampered session."""
    from uuid import UUID

    from agentforge.persistence.db import get_session_factory
    from agentforge.persistence.event_log import EventLog
    from agentforge.persistence.session_store import SessionStore
    from agentforge.persistence.workspace import WorkspaceManager

    session_body = _create_author_session(app_client)
    sid = UUID(session_body["id"])
    app_client.post(
        f"/sessions/{sid}/files",
        files={"file": ("y.csv", io.BytesIO(b"c,d\n3,4\n"), "text/csv")},
    )

    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(Path(session_body["workspace_path"]).parent)
        log = EventLog(workspace_manager=wm)
        store = SessionStore(db=db, workspace_manager=wm, event_log=log)
        session, manifest, warnings = store.resume_check(sid)
        assert warnings == []
        assert session.id == sid
        assert manifest.session_id == sid
        assert "uploads/y.csv" in manifest.file_hashes
    finally:
        db.close()
