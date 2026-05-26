"""Per-session workspace directory management.

Each session has a directory under ``${WORKSPACES_ROOT}/${session_id}/`` with
the canonical layout from ``ARCHITECTURE.md`` §5. This module:

  - Allocates the workspace on session creation.
  - Writes and reads ``manifest.json`` (the resume anchor).
  - Verifies file hashes on resume (tamper-detection contract).
  - Enforces path discipline: callers cannot escape ``${workspace_path}``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from agentforge.schemas import BudgetStatus, ResumeManifest, SessionStatus, Workflow

# Subdirs created at allocation time. The layout is fixed; tools assume it.
_SUBDIRS: tuple[str, ...] = (
    "uploads",
    "generated",
    "working",
    "outputs",
    "outputs/_logs",
    "reports",
)


class WorkspaceError(Exception):
    """Raised on workspace integrity or path-discipline violations."""


class WorkspaceManager:
    """Allocate, locate, and verify per-session workspace directories."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def allocate(
        self,
        session_id: UUID,
        workflow: Workflow,
        started_at: datetime | None = None,
    ) -> Path:
        """Create the session workspace and write the initial manifest.

        Returns the absolute path to the workspace root.
        """
        ws = self.path_for(session_id)
        if ws.exists():
            raise WorkspaceError(f"workspace already exists: {ws}")

        ws.mkdir(parents=True)
        for sub in _SUBDIRS:
            (ws / sub).mkdir(parents=True, exist_ok=True)

        # events.jsonl created empty; event_log writes append-only.
        (ws / "events.jsonl").touch()

        ts = started_at or datetime.now(UTC)
        manifest = ResumeManifest(
            session_id=session_id,
            workflow=workflow,
            status=SessionStatus.CREATED,
            current_phase=None,
            current_step=0,
            started_at=ts,
            updated_at=ts,
            workspace_path=str(ws),
            schema_version=1,
            budget=BudgetStatus(),
            file_hashes={},
        )
        self.write_manifest(session_id, manifest)
        return ws

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def path_for(self, session_id: UUID) -> Path:
        """Return the workspace path for a session id (existence not checked)."""
        return self.root / str(session_id)

    def get(self, session_id: UUID) -> Path:
        """Return the workspace path, raising if it does not exist."""
        ws = self.path_for(session_id)
        if not ws.is_dir():
            raise WorkspaceError(f"workspace not found for session {session_id}")
        return ws

    def exists(self, session_id: UUID) -> bool:
        return self.path_for(session_id).is_dir()

    # ------------------------------------------------------------------
    # Manifest IO
    # ------------------------------------------------------------------

    def manifest_path(self, session_id: UUID) -> Path:
        return self.get(session_id) / "manifest.json"

    def read_manifest(self, session_id: UUID) -> ResumeManifest:
        path = self.manifest_path(session_id)
        if not path.is_file():
            raise WorkspaceError(f"manifest.json missing: {path}")
        return ResumeManifest.model_validate_json(path.read_text())

    def write_manifest(self, session_id: UUID, manifest: ResumeManifest) -> None:
        """Atomically write manifest.json (temp file + rename)."""
        path = self.path_for(session_id) / "manifest.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(manifest.model_dump_json(indent=2))
        tmp.replace(path)

    def update_manifest(self, session_id: UUID, **fields: object) -> ResumeManifest:
        """Read, mutate, write back. Updates ``updated_at`` automatically."""
        current = self.read_manifest(session_id)
        data = current.model_dump()
        data.update(fields)
        data["updated_at"] = datetime.now(UTC)
        manifest = ResumeManifest.model_validate(data)
        self.write_manifest(session_id, manifest)
        return manifest

    # ------------------------------------------------------------------
    # Path discipline
    # ------------------------------------------------------------------

    def resolve_in(self, session_id: UUID, relative: str | Path) -> Path:
        """Resolve a path relative to the workspace.

        Rejects absolute paths and any path that resolves outside the
        workspace (e.g., ``..`` segments). This is the chokepoint
        every tool that touches files must go through (enforces INV-5).
        """
        ws = self.get(session_id)
        candidate = Path(relative)
        if candidate.is_absolute():
            raise WorkspaceError(
                f"absolute paths not permitted in workspace tools: {candidate}"
            )
        resolved = (ws / candidate).resolve()
        try:
            resolved.relative_to(ws)
        except ValueError as exc:
            raise WorkspaceError(
                f"path escapes workspace: {relative} → {resolved}"
            ) from exc
        return resolved

    # ------------------------------------------------------------------
    # Resume integrity
    # ------------------------------------------------------------------

    def verify_uploads_against_manifest(
        self, session_id: UUID
    ) -> tuple[bool, dict[str, str]]:
        """Compare on-disk hashes for ``uploads/*`` against manifest.file_hashes.

        Returns ``(intact, drifts)`` where ``drifts`` maps relative paths to
        a human-readable description of the mismatch. ``intact`` is true iff
        every recorded hash matches and no recorded file is missing.
        """
        ws = self.get(session_id)
        manifest = self.read_manifest(session_id)
        drifts: dict[str, str] = {}
        for rel, recorded_hash in manifest.file_hashes.items():
            f = ws / rel
            if not f.is_file():
                drifts[rel] = f"recorded file missing on disk (was {recorded_hash[:12]}…)"
                continue
            current = hash_file(f)
            if current != recorded_hash:
                drifts[rel] = (
                    f"hash drift {recorded_hash[:12]}… → {current[:12]}…"
                )
        return (not drifts, drifts)


# ----------------------------------------------------------------------
# Hashing utilities
# ----------------------------------------------------------------------


def hash_bytes(data: bytes) -> str:
    """Return the sha256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path, chunk_size: int = 65536) -> str:
    """Return the sha256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
