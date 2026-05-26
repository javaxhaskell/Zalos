"""CLI entry-point for the eval runner.

Invoked via ``make eval`` → ``uv run python -m agentforge.evals``.

Allocates a temp SQLite DB + a temp workspaces root so the CLI run
doesn't pollute the dev DB. Walks all three bundled scenarios,
prints a one-line-per-scenario summary, and exits 0 on 3/3 pass / 1
otherwise. The HTTP `POST /evals/run` endpoint is the in-app path;
this CLI exists so a operator can run the suite headless without
booting the API server.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Invoked via `PYTHONPATH=src uv run python -m agentforge.evals` (see
# the Makefile `eval` target). The PYTHONPATH dance is the same
# workaround scripts/snapshot_openapi.py uses for `uv run python` not
# always picking up the editable-install .pth file.


def main() -> int:
    """Run all bundled scenarios; return exit code 0 on full pass."""
    # Bootstrap a clean DB + workspaces root before importing the app
    # so module-level Settings caching picks them up.
    tmpdir = Path(tempfile.mkdtemp(prefix="agentforge-eval-cli-"))
    db_path = tmpdir / "eval.db"
    workspaces = tmpdir / "workspaces"
    workspaces.mkdir()

    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["WORKSPACES_ROOT"] = str(workspaces)

    # Locate the repo root from this file (apps/api/src/agentforge/evals/__main__.py)
    repo_root = Path(__file__).resolve().parents[5]
    os.environ.setdefault(
        "TEMPLATES_ROOT", str(repo_root / "templates")
    )
    os.environ.setdefault(
        "FIXTURES_BROKEN_AGENTS_ROOT",
        str(repo_root / "fixtures" / "broken_agents"),
    )

    # Resets caches so the new env vars apply.
    import agentforge.config as config_module
    import agentforge.persistence.db as db_module

    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None

    from agentforge.config import get_settings
    from agentforge.evals.runner import EvalRunner
    from agentforge.persistence import models as _models  # noqa: F401
    from agentforge.persistence.db import Base, init_engine
    from agentforge.persistence.workspace import WorkspaceManager

    engine = init_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)

    from agentforge.persistence.db import get_session_factory

    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(workspaces)
        settings = get_settings()
        runner = EvalRunner(db=db, workspace_manager=wm, settings=settings)
        summary = asyncio.run(runner.run_all())
    finally:
        db.close()

    # One-line-per-scenario summary, then aggregate.
    print()
    print(f"Eval run {summary.id}")
    print(f"  started_at:   {summary.started_at.isoformat()}")
    if summary.completed_at:
        wall = (summary.completed_at - summary.started_at).total_seconds()
        print(f"  completed_at: {summary.completed_at.isoformat()}")
        print(f"  wall_seconds: {wall:.2f}")
    print()
    print("Per scenario:")
    for r in summary.results:
        status = "PASS" if r.passed else "FAIL"
        line = f"  [{status}] {r.scenario_id:<28} latency={r.latency_ms:>6} ms"
        if r.failure_reason:
            line += f"  reason={r.failure_reason}"
        print(line)
    print()
    print(
        f"Total: {summary.passed}/{summary.total} pass "
        f"({summary.failed} fail)"
    )

    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
