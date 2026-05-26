"""File upload + artifact download endpoints.

Phase 3 lights up:
  - POST /sessions/{session_id}/files: upload a CSV or XLSX into the session
    workspace. Enforces size, MIME, total-upload, and file-count budgets
    BEFORE bytes are persisted. Emits FILE_UPLOADED.

Phase 4/5/10 light up artifact download + archive ZIP.
"""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, status
from fastapi.responses import FileResponse

from agentforge.api.deps import (
    get_artifact_store,
    get_event_log,
    get_session_store,
    get_settings_dep,
    get_workspace_manager,
)
from agentforge.api.errors import (
    APIError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    UploadLimitExceededError,
)
from agentforge.config import Settings
from agentforge.persistence.archive import ARCHIVE_FILENAME, build_archive
from agentforge.persistence.artifact_store import ArtifactStore
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.session_store import SessionNotFoundError, SessionStore
from agentforge.persistence.workspace import WorkspaceError, WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ArtifactType,
    EventKind,
    FileUploadResponse,
)

router = APIRouter(prefix="/sessions/{session_id}", tags=["files"])

# Path prefixes inside the workspace that a user is allowed to download
# via /artifacts/{...}. Anything outside these is rejected (INV-5 path
# discipline applied symmetrically to reads).
_ARTIFACT_PATH_PREFIXES: tuple[str, ...] = (
    "reports/",
    "outputs/",
    "generated/",
    "working/",
)


def _session_not_found(session_id: UUID) -> APIError:
    err = APIError(
        message=f"session {session_id} not found",
        session_id=session_id,
    )
    err.http_status = 404
    return err


@router.post(
    "/files",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    session_id: Annotated[UUID, Path()],
    file: UploadFile,
    settings: Settings = Depends(get_settings_dep),
    store: SessionStore = Depends(get_session_store),
    artifacts: ArtifactStore = Depends(get_artifact_store),
) -> FileUploadResponse:
    """Upload a CSV or XLSX into the session workspace.

    Validates filename extension and MIME type BEFORE reading the body;
    enforces single-file and per-session total size limits; rejects
    uploads if the session has reached its file-count budget.
    """
    try:
        session = store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _session_not_found(session_id) from exc

    # 1. Filename / MIME validation
    filename = file.filename or ""
    if not ArtifactStore.is_allowed_filename(filename):
        raise UnsupportedFileTypeError(
            message=(
                "We can only read .csv and .xlsx files in this version. "
                "Convert and try again."
            ),
            session_id=session_id,
            details={"filename": filename, "content_type": file.content_type},
        )
    mime = file.content_type or "application/octet-stream"
    if not ArtifactStore.is_allowed_mime(mime):
        raise UnsupportedFileTypeError(
            message=(
                "We can only read CSV and Excel uploads. The browser reported "
                f"content type {mime!r}."
            ),
            session_id=session_id,
            details={"filename": filename, "content_type": mime},
        )

    # 2. File-count budget
    if session.budget.file_count >= session.budget.file_count_limit:
        raise UploadLimitExceededError(
            message=(
                "This session has reached its upload limit. Remove a file before "
                "adding another, or finalise to start a new session."
            ),
            session_id=session_id,
            details={
                "file_count": session.budget.file_count,
                "file_count_limit": session.budget.file_count_limit,
            },
        )

    # 3. Read body with per-file size enforcement (avoid loading too-large
    #    files into memory before rejecting them).
    max_bytes = settings.budget_file_upload_max_bytes
    data = await file.read()
    if len(data) > max_bytes:
        raise FileTooLargeError(
            message=(
                f"This file is larger than the {max_bytes // (1024 * 1024)} MB "
                "limit. Please upload a smaller sample."
            ),
            session_id=session_id,
            details={
                "filename": filename,
                "size_bytes": len(data),
                "limit_bytes": max_bytes,
            },
        )

    # 4. Per-session total size enforcement
    total_limit = settings.budget_files_total_max_bytes
    prior_uploads = artifacts.list_uploads(session_id)
    prior_total = sum(u.size_bytes for u in prior_uploads)
    if prior_total + len(data) > total_limit:
        raise UploadLimitExceededError(
            message=(
                "Uploading this file would exceed the per-session upload "
                f"limit ({total_limit // (1024 * 1024)} MB total). Remove a file "
                "to make room."
            ),
            session_id=session_id,
            details={
                "prior_total_bytes": prior_total,
                "incoming_bytes": len(data),
                "limit_bytes": total_limit,
            },
        )

    # 5. Persist
    uploaded = artifacts.store_upload(
        session_id=session_id,
        filename=filename,
        mime=mime,
        data=data,
    )
    store.record_uploaded_file(session_id)

    relative_path = f"uploads/{uploaded.filename}"
    return FileUploadResponse(
        uploaded_file_id=uploaded.id,
        filename=uploaded.filename,
        size_bytes=uploaded.size_bytes,
        hash_sha256=uploaded.hash_sha256,
        relative_path=relative_path,
        uploaded_at=uploaded.uploaded_at,
    )


@router.get("/artifacts/{relative_path:path}")
async def download_artifact(
    session_id: Annotated[UUID, Path()],
    relative_path: str,
    store: SessionStore = Depends(get_session_store),
    wm: WorkspaceManager = Depends(get_workspace_manager),
) -> FileResponse:
    """Stream an individual artifact from the session workspace.

    Allowed prefixes: ``reports/``, ``outputs/``, ``generated/``,
    ``working/``. Everything else is rejected at the boundary
    (uploads/ excluded — they're the user's own files; events.jsonl
    + manifest.json served via /audit/export and the archive).

    INV-5: the path is resolved through ``WorkspaceManager.resolve_in``
    so ``..`` and absolute paths are rejected. Content-Type is
    inferred from the extension (``text/markdown`` for ``.md``,
    ``text/csv`` for ``.csv``, etc.).
    """
    try:
        store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _session_not_found(session_id) from exc

    if not any(relative_path.startswith(p) for p in _ARTIFACT_PATH_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "artifact_path_forbidden",
                "message": (
                    f"path {relative_path!r} is outside the artifact-serving "
                    f"surface. Allowed prefixes: {', '.join(_ARTIFACT_PATH_PREFIXES)}"
                ),
            },
        )

    try:
        resolved = wm.resolve_in(session_id, relative_path)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "artifact_path_invalid",
                "message": str(exc),
            },
        ) from exc

    if not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "artifact_not_found",
                "message": f"no artifact at {relative_path!r}",
            },
        )

    media_type, _ = mimetypes.guess_type(resolved.name)
    if resolved.suffix == ".md":
        media_type = "text/markdown; charset=utf-8"
    return FileResponse(
        resolved,
        media_type=media_type or "application/octet-stream",
        filename=resolved.name,
    )


@router.get("/archive.zip")
async def download_archive(
    session_id: Annotated[UUID, Path()],
    store: SessionStore = Depends(get_session_store),
    wm: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> FileResponse:
    """Stream the full session archive ZIP.

    If ``archive.zip`` does not exist yet (the agent never called
    ``archive_workspace``), build it now and emit an
    ``ARTIFACT_GENERATED`` event. Subsequent calls return the cached
    file. Sessions with no load-bearing contents (no reports / outputs
    / generated / working) yield 409 — the wizard surfaces this as
    "nothing to archive yet."
    """
    try:
        store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _session_not_found(session_id) from exc

    workspace = wm.get(session_id)
    archive_path = workspace / ARCHIVE_FILENAME
    if not archive_path.is_file():
        try:
            result = build_archive(session_id=session_id, workspace_manager=wm)
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "archive_empty",
                    "message": str(exc),
                },
            ) from exc
        event_log.append(
            session_id=session_id,
            kind=EventKind.ARTIFACT_GENERATED,
            actor_type=ActorType.USER,
            payload={
                "artifact_type": ArtifactType.ARCHIVE.value,
                "path": result.relative_path,
                "size_bytes": result.size_bytes,
                "hash_sha256": result.hash_sha256,
                "file_count": result.file_count,
                "via": "http_endpoint",
            },
            step=0,
            ts=datetime.now(UTC),
        )

    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"agentforge-session-{session_id}.zip",
    )
