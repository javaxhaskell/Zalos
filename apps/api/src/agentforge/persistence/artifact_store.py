"""File persistence for uploads + downstream artifacts.

The artifact store is responsible for:
  - Persisting uploaded CSV/XLSX files under ``${workspace_path}/uploads/``.
  - Hashing every written file (sha256) and recording the hash in both
    the DB and the manifest (so resume can verify integrity).
  - Enforcing per-file and per-session size limits at the storage edge
    (the upload endpoint validates *before* calling the store).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session as DBSession

from agentforge.persistence.event_log import EventLog
from agentforge.persistence.models import UploadedFileRow
from agentforge.persistence.workspace import WorkspaceManager, hash_bytes
from agentforge.schemas import ActorType, EventKind, UploadedFile

# Allowed input MIME types and extensions (matches ADR-0008 scope).
ALLOWED_MIME = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",  # browsers occasionally send this for .csv
}
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls"}


class ArtifactStoreError(Exception):
    """Raised on artifact-store integrity violations."""


class ArtifactStore:
    """Persist uploaded files + manage downstream artifacts on disk + in the DB."""

    def __init__(
        self,
        db: DBSession,
        workspace_manager: WorkspaceManager,
        event_log: EventLog,
    ) -> None:
        self.db = db
        self.wm = workspace_manager
        self.event_log = event_log

    # ------------------------------------------------------------------
    # Validation helpers (called by the route layer before bytes are read)
    # ------------------------------------------------------------------

    @staticmethod
    def is_allowed_filename(filename: str) -> bool:
        suffix = Path(filename).suffix.lower()
        return suffix in ALLOWED_SUFFIXES

    @staticmethod
    def is_allowed_mime(mime: str) -> bool:
        return mime in ALLOWED_MIME

    # ------------------------------------------------------------------
    # Upload path
    # ------------------------------------------------------------------

    def store_upload(
        self,
        *,
        session_id: UUID,
        filename: str,
        mime: str,
        data: bytes,
    ) -> UploadedFile:
        """Persist an uploaded file under ``${workspace}/uploads/`` and record it.

        Caller is responsible for size + MIME enforcement *before* calling
        (route layer rejects too-large or wrong-MIME uploads upstream).
        This function still re-checks for defence in depth.
        """
        if not self.is_allowed_filename(filename):
            raise ArtifactStoreError(
                f"disallowed file extension: {filename!r}"
            )

        # Strip path components from user-supplied filename.
        safe_name = Path(filename).name
        relative = Path("uploads") / safe_name
        target = self.wm.resolve_in(session_id, relative)

        # If a file with the same name already exists, suffix with a uuid
        # rather than overwriting (uploads are write-once by contract).
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            target = target.with_name(f"{stem}_{uuid4().hex[:8]}{suffix}")
            safe_name = target.name
            relative = Path("uploads") / safe_name

        target.write_bytes(data)
        sha = hash_bytes(data)

        now = datetime.now(UTC)
        row = UploadedFileRow(
            id=str(uuid4()),
            session_id=str(session_id),
            filename=safe_name,
            mime=mime,
            size_bytes=len(data),
            hash_sha256=sha,
            storage_path=str(target),
            uploaded_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        # Update the manifest file_hashes for resume integrity.
        manifest = self.wm.read_manifest(session_id)
        manifest_hashes = dict(manifest.file_hashes)
        manifest_hashes[str(relative)] = sha
        self.wm.update_manifest(session_id, file_hashes=manifest_hashes)

        # Emit FILE_UPLOADED event.
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.FILE_UPLOADED,
            actor_type=ActorType.USER,
            payload={
                "uploaded_file_id": row.id,
                "filename": safe_name,
                "size_bytes": len(data),
                "hash_sha256": sha,
                "relative_path": str(relative),
            },
        )

        return UploadedFile(
            id=UUID(row.id),
            session_id=session_id,
            filename=safe_name,
            mime=mime,
            size_bytes=len(data),
            hash_sha256=sha,
            storage_path=str(target),
            uploaded_at=now,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_uploads(self, session_id: UUID) -> list[UploadedFile]:
        rows = (
            self.db.query(UploadedFileRow)
            .filter(UploadedFileRow.session_id == str(session_id))
            .order_by(UploadedFileRow.uploaded_at.asc())
            .all()
        )
        return [
            UploadedFile(
                id=UUID(r.id),
                session_id=UUID(r.session_id),
                filename=r.filename,
                mime=r.mime,
                size_bytes=r.size_bytes,
                hash_sha256=r.hash_sha256,
                storage_path=r.storage_path,
                uploaded_at=r.uploaded_at,
            )
            for r in rows
        ]
