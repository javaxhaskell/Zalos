"""Bundled fixture loader (BP9).

The repair wizard's InputStage shows a small picker so a finance user
can drive the demo against ``invoice_aging_v1`` without packaging a
ZIP themselves. This endpoint mirrors the in-Python fixture-staging
that ``test_repair_flow_e2e`` performs: it copies the named fixture
into ``${workspace}/working/`` and, if the fixture ships
``data/expected_output.csv``, stages it into ``${workspace}/evals/``
so ``validate_output`` finds the golden.

Safety:

  * Only fixtures whose directory sits directly under
    :attr:`Settings.fixtures_broken_agents_root` can be loaded. The
    fixture name is path-name-only — slashes, dots, traversal segments
    are rejected at the boundary.
  * The workspace destination is resolved through
    ``WorkspaceManager.resolve_in`` (INV-5).
  * Refuses to overwrite a non-empty ``working/`` (the repair flow
    cannot meaningfully run twice in the same workspace; re-loading is
    a destructive operation).
"""

from __future__ import annotations

import io
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi import Path as PathParam

from agentforge.api.deps import (
    get_event_log,
    get_session_store,
    get_settings_dep,
    get_workspace_manager,
)
from agentforge.config import Settings
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.session_store import SessionNotFoundError, SessionStore
from agentforge.persistence.workspace import WorkspaceError, WorkspaceManager
from agentforge.schemas import (
    ActorType,
    EventKind,
    LoadFixtureResponse,
    UploadAgentZipResponse,
)

# ZIP-upload safety caps (BP-final). Adjust via Settings if a real
# customer's broken agent breaks these — they're sized for the demo
# fixture (~14 files, ~30 KB) with headroom.
_MAX_ZIP_FILE_COUNT = 100
"""Maximum entries (excluding directories) extracted from one ZIP."""
_MAX_ZIP_ENTRY_BYTES = 10 * 1024 * 1024  # 10 MiB per uncompressed entry
"""Per-entry uncompressed-size cap; defends against zip-bombs at the entry level."""
_MAX_ZIP_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MiB uncompressed total
"""Aggregate uncompressed-size cap; second zip-bomb defence."""
_EXCLUDED_ENTRY_NAMES: frozenset[str] = frozenset(
    {
        "__MACOSX",
        ".DS_Store",
        "Thumbs.db",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        "CACHEDIR.TAG",
        "lastfailed",
        "nodeids",
        "stepwise",
    }
)
"""Editor / OS / Python build detritus that has no place in a repair
workspace. Applied to both ZIP entries (any path part) and fixture
copytree."""
_EXCLUDED_ENTRY_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")

router = APIRouter(prefix="/sessions", tags=["fixtures"])

_EXCLUDED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "README.md",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".DS_Store",
        ".git",
    }
)
"""Top-level items inside ``fixtures/broken_agents/<name>/`` that are
fixture-internal and not part of the agent the user sees."""


def _ignore_junk(_src: str, names: list[str]) -> list[str]:
    """``shutil.copytree`` ignore callback that drops dev/OS junk."""
    return [
        n
        for n in names
        if n in _EXCLUDED_ENTRY_NAMES
        or any(n.endswith(suf) for suf in _EXCLUDED_ENTRY_SUFFIXES)
    ]

_FIXTURE_NAME_OK_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _validate_fixture_name(name: str) -> None:
    """Reject path-like names; whitelist only basename-safe characters."""
    if not name or any(ch not in _FIXTURE_NAME_OK_CHARS for ch in name):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_fixture_name",
                "message": (
                    f"fixture name {name!r} contains characters other "
                    "than [A-Za-z0-9_-]; refusing"
                ),
            },
        )


@router.post(
    "/{session_id}/load_fixture/{fixture_name}",
    response_model=LoadFixtureResponse,
)
async def load_fixture(
    session_id: Annotated[UUID, PathParam()],
    fixture_name: Annotated[str, PathParam(min_length=1, max_length=64)],
    settings: Settings = Depends(get_settings_dep),
    store: SessionStore = Depends(get_session_store),
    wm: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> LoadFixtureResponse:
    """Copy a bundled broken-agent fixture into the session's workspace.

    Emits one ``decision_input`` event recording the fixture choice
    plus one ``file_uploaded`` event per copied file so the audit log
    + wizard upload list reflect the load.
    """
    _validate_fixture_name(fixture_name)

    try:
        store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "session_not_found",
                "message": f"session {session_id} not found",
            },
        ) from exc

    # Synchronous path IO inside an async handler is fine here: the
    # working tree is tiny (a single broken-agent fixture, <50 files),
    # and the rest of this app uses sync SQLAlchemy in the same shape.
    # Switching to trio/anyio.Path would add async churn without changing
    # behaviour. (ruff: noqa for the same reason.)
    fixtures_root = Path(settings.fixtures_broken_agents_root).resolve()  # noqa: ASYNC240
    fixture_src = (fixtures_root / fixture_name).resolve()
    # Defence-in-depth: also confirm the resolved source still lives
    # under the configured root after `..`/symlink normalisation.
    try:
        fixture_src.relative_to(fixtures_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_fixture_name",
                "message": (
                    "fixture resolution escaped the bundled-fixtures "
                    "root; refusing"
                ),
            },
        ) from exc

    if not fixture_src.is_dir():
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "fixture_not_found",
                "message": (
                    f"no bundled fixture named {fixture_name!r} under "
                    f"{fixtures_root}"
                ),
            },
        )

    working_dir = wm.resolve_in(session_id, "working")
    working_dir.mkdir(parents=True, exist_ok=True)
    if any(working_dir.iterdir()):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "working_not_empty",
                "message": (
                    "working/ is not empty; refusing to re-load a "
                    "fixture. Start a fresh session to switch fixtures."
                ),
            },
        )

    now = datetime.now(UTC)
    workspace_root = wm.get(session_id)
    copied: list[Path] = []
    for item in sorted(fixture_src.iterdir(), key=lambda p: p.name):
        if item.name in _EXCLUDED_TOP_LEVEL:
            continue
        if any(item.name.endswith(suf) for suf in _EXCLUDED_ENTRY_SUFFIXES):
            continue
        dest = working_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=_ignore_junk)
            # Belt-and-braces: scrub any junk that slipped past the
            # ignore callback (older shutil semantics on nested dirs).
            for junk in list(dest.rglob("*")):
                if junk.name in _EXCLUDED_ENTRY_NAMES or any(
                    junk.name.endswith(s) for s in _EXCLUDED_ENTRY_SUFFIXES
                ):
                    if junk.is_dir():
                        shutil.rmtree(junk, ignore_errors=True)
                    else:
                        junk.unlink(missing_ok=True)
            copied.extend(p for p in dest.rglob("*") if p.is_file())
        else:
            shutil.copy2(item, dest)
            copied.append(dest)

    files_copied = sorted(
        str(p.relative_to(workspace_root)).replace("\\", "/") for p in copied
    )

    staged_golden: str | None = None
    bundled_golden = fixture_src / "data" / "expected_output.csv"
    if bundled_golden.is_file():
        evals_dir = wm.resolve_in(session_id, "evals")
        evals_dir.mkdir(parents=True, exist_ok=True)
        staged_dest = evals_dir / "expected_output.csv"
        shutil.copy2(bundled_golden, staged_dest)
        staged_golden = str(
            staged_dest.relative_to(workspace_root)
        ).replace("\\", "/")

    # Record the decision + each file. INV-9 — synthetic data only;
    # filenames + sizes are fine to log.
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={
            "kind": "fixture_loaded",
            "fixture_name": fixture_name,
            "file_count": len(files_copied),
            "staged_golden_path": staged_golden,
        },
        step=0,
        ts=now,
    )
    for rel in files_copied:
        abs_path = workspace_root / rel
        event_log.append(
            session_id=session_id,
            kind=EventKind.FILE_UPLOADED,
            actor_type=ActorType.SYSTEM,
            payload={
                "filename": Path(rel).name,
                "relative_path": rel,
                "size_bytes": abs_path.stat().st_size,
                "source": f"fixture:{fixture_name}",
            },
            step=0,
            ts=now,
        )

    return LoadFixtureResponse(
        session_id=session_id,
        fixture_name=fixture_name,
        files_copied=files_copied,
        staged_golden_path=staged_golden,
        loaded_at=now,
    )


# ---------------------------------------------------------------------------
# Upload an arbitrary broken-agent ZIP (BP-final polish).
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/upload_agent_zip",
    response_model=UploadAgentZipResponse,
)
async def upload_agent_zip(
    session_id: Annotated[UUID, PathParam()],
    file: UploadFile,
    store: SessionStore = Depends(get_session_store),
    wm: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> UploadAgentZipResponse:
    """Extract a user-supplied broken-agent ZIP into ``working/``.

    Complementary to :func:`load_fixture` — the picker handles
    bundled demos, this endpoint handles real customer ZIPs. Same
    downstream semantics (populates ``working/`` + stages
    ``evals/expected_output.csv`` if the ZIP carries one), same
    INV-5 path discipline.

    Defences:
      * **Zip-slip:** every entry's path is resolved through
        :meth:`WorkspaceManager.resolve_in`; any entry that would
        write outside ``working/`` triggers a rollback + 400.
      * **Zip-bomb:** per-entry cap (10 MiB uncompressed) + aggregate
        cap (100 MiB) + max-file-count cap (100). The aggregate is
        tracked as we extract — we stop + roll back when the limit
        is hit rather than after.
      * **MIME / name:** content type must be ``application/zip`` /
        ``application/x-zip-compressed`` / ``application/octet-stream``
        (browsers vary). Filename extension must be ``.zip``.

    On any failure the partially-extracted ``working/`` is cleared so
    the session remains in a clean state for a retry.
    """
    try:
        store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "session_not_found",
                "message": f"session {session_id} not found",
            },
        ) from exc

    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=415,
            detail={
                "error_code": "unsupported_file_type",
                "message": "expected a .zip archive",
                "filename": filename,
            },
        )
    mime = (file.content_type or "").lower()
    if mime not in (
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "",
    ):
        raise HTTPException(
            status_code=415,
            detail={
                "error_code": "unsupported_file_type",
                "message": f"unexpected content-type for .zip: {mime!r}",
            },
        )

    working_dir = wm.resolve_in(session_id, "working")
    working_dir.mkdir(parents=True, exist_ok=True)
    if any(working_dir.iterdir()):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "working_not_empty",
                "message": (
                    "working/ is not empty; start a fresh session to "
                    "upload a different ZIP."
                ),
            },
        )

    body = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_zip",
                "message": f"not a valid ZIP archive: {exc}",
            },
        ) from exc

    workspace_root = wm.get(session_id)
    extracted: list[Path] = []
    total_bytes = 0
    file_count = 0
    try:
        with zf:
            members = [
                m
                for m in zf.infolist()
                if not m.is_dir()
                and not any(
                    part in _EXCLUDED_ENTRY_NAMES for part in Path(m.filename).parts
                )
                and not any(
                    m.filename.endswith(suf) for suf in _EXCLUDED_ENTRY_SUFFIXES
                )
            ]
            if len(members) > _MAX_ZIP_FILE_COUNT:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error_code": "zip_too_many_files",
                        "message": (
                            f"ZIP contains {len(members)} files; cap is "
                            f"{_MAX_ZIP_FILE_COUNT}"
                        ),
                    },
                )
            for member in members:
                # Resolve through WorkspaceManager to defeat zip-slip
                # (entries like ../../etc/passwd or absolute paths).
                rel = Path("working") / member.filename
                try:
                    target = wm.resolve_in(session_id, str(rel))
                except WorkspaceError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error_code": "zip_path_escape",
                            "message": (
                                f"ZIP entry {member.filename!r} escapes the "
                                "workspace; refusing"
                            ),
                        },
                    ) from exc
                if member.file_size > _MAX_ZIP_ENTRY_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error_code": "zip_entry_too_large",
                            "message": (
                                f"ZIP entry {member.filename!r} is "
                                f"{member.file_size} bytes uncompressed; "
                                f"cap is {_MAX_ZIP_ENTRY_BYTES}"
                            ),
                        },
                    )
                total_bytes += member.file_size
                if total_bytes > _MAX_ZIP_TOTAL_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error_code": "zip_total_too_large",
                            "message": (
                                f"ZIP exceeds aggregate {_MAX_ZIP_TOTAL_BYTES}-byte "
                                "uncompressed cap"
                            ),
                        },
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dest:
                    shutil.copyfileobj(src, dest, length=64 * 1024)
                extracted.append(target)
                file_count += 1
    except HTTPException:
        # Roll back the partial extraction so the session is reusable.
        import contextlib

        for p in extracted:
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        # Best-effort dir cleanup (empty dirs left over).
        for sub in sorted(working_dir.rglob("*"), reverse=True):
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        raise

    files_extracted = sorted(
        str(p.relative_to(workspace_root)).replace("\\", "/") for p in extracted
    )

    # If the uploaded ZIP carries a data/expected_output.csv (a common
    # convention for repair fixtures), stage it into evals/ so
    # validate_output finds it without the agent having to move it.
    staged_golden: str | None = None
    bundled_golden = working_dir / "data" / "expected_output.csv"
    if bundled_golden.is_file():
        evals_dir = wm.resolve_in(session_id, "evals")
        evals_dir.mkdir(parents=True, exist_ok=True)
        staged_dest = evals_dir / "expected_output.csv"
        shutil.copy2(bundled_golden, staged_dest)
        staged_golden = str(
            staged_dest.relative_to(workspace_root)
        ).replace("\\", "/")

    now = datetime.now(UTC)
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={
            "kind": "agent_zip_uploaded",
            "archive_filename": filename,
            "file_count": file_count,
            "total_uncompressed_bytes": total_bytes,
            "staged_golden_path": staged_golden,
        },
        step=0,
        ts=now,
    )
    for rel_path in files_extracted:
        abs_path = workspace_root / rel_path
        event_log.append(
            session_id=session_id,
            kind=EventKind.FILE_UPLOADED,
            actor_type=ActorType.USER,
            payload={
                "filename": Path(rel_path).name,
                "relative_path": rel_path,
                "size_bytes": abs_path.stat().st_size,
                "source": f"zip:{filename}",
            },
            step=0,
            ts=now,
        )

    return UploadAgentZipResponse(
        session_id=session_id,
        archive_filename=filename,
        files_extracted=files_extracted,
        staged_golden_path=staged_golden,
        total_uncompressed_bytes=total_bytes,
        uploaded_at=now,
    )
