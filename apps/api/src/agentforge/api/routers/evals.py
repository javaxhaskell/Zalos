"""Eval endpoints (BP10b).

  * ``POST /evals/run`` — runs every bundled scenario through the
    real orchestrator. Author scenarios use the configured model
    client; Repair scenarios use fixture evidence scripts. The route
    persists to ``eval_runs`` + ``eval_results`` and returns the
    aggregate :class:`EvalRunSummary` (so the wizard can render the
    pass/fail breakdown immediately rather than polling).
  * ``GET /evals/latest`` — returns the most recently completed run.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc
from sqlalchemy.orm import Session as DBSession

from agentforge.api.deps import (
    get_db_session,
    get_model_client,
    get_settings_dep,
    get_workspace_manager,
)
from agentforge.config import Settings
from agentforge.evals.runner import run_all_scenarios
from agentforge.models import ModelClient
from agentforge.persistence.models import EvalResultRow, EvalRunRow
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import EvalRunResult, EvalRunSummary

router = APIRouter(prefix="/evals", tags=["evals"])


@router.post(
    "/run",
    response_model=EvalRunSummary,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_eval_run(
    db: DBSession = Depends(get_db_session),
    wm: WorkspaceManager = Depends(get_workspace_manager),
    settings: Settings = Depends(get_settings_dep),
    model_client: ModelClient = Depends(get_model_client),
) -> EvalRunSummary:
    """Run every bundled scenario synchronously; persist + return the summary.

    The runner takes <10s for the three current scenarios on a warm
    machine, so we run inline rather than via the BP8 background
    supervisor. If the suite grows past comfortable response times,
    flip this to background + polling.
    """
    return await run_all_scenarios(
        db=db,
        workspace_manager=wm,
        settings=settings,
        author_model_client=model_client,
    )


@router.get(
    "/latest",
    response_model=EvalRunSummary,
)
async def get_latest_eval_run(
    db: DBSession = Depends(get_db_session),
) -> EvalRunSummary:
    """Return the most recent EvalRunSummary, or 404 if none recorded."""
    row = (
        db.query(EvalRunRow)
        .order_by(desc(EvalRunRow.started_at))
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "no_eval_runs",
                "message": "No eval run has been recorded yet. POST /evals/run first.",
            },
        )
    result_rows = (
        db.query(EvalResultRow)
        .filter(EvalResultRow.run_id == row.id)
        .all()
    )
    results = [
        EvalRunResult(
            scenario_id=r.scenario_id,
            passed=r.passed,
            latency_ms=r.latency_ms,
            cost_usd=r.cost_usd,
            failure_reason=r.failure_reason,
            diff_path=r.diff_path,
        )
        for r in result_rows
    ]
    per_tag: dict[str, dict[str, int]] = {}
    # Best-effort: we don't persist per-tag breakdown on the row, so
    # recompute it from the scenario_id prefix. A-* = author,
    # R-* = repair, ADV-* = adversarial.
    for r in result_rows:
        tag = _tag_from_scenario_id(r.scenario_id)
        bucket = per_tag.setdefault(tag, {"passed": 0, "failed": 0})
        bucket["passed" if r.passed else "failed"] += 1
    return EvalRunSummary(
        id=UUID(row.id),
        started_at=row.started_at,
        completed_at=row.completed_at,
        total=row.total,
        passed=row.passed,
        failed=row.failed,
        per_tag=per_tag,
        results=results,
    )


def _tag_from_scenario_id(scenario_id: str) -> str:
    """Recover the EvalKind tag from the scenario id prefix."""
    if scenario_id.startswith("A-"):
        return "author"
    if scenario_id.startswith("R-"):
        return "repair"
    if scenario_id.startswith("ADV-"):
        return "adversarial"
    return "other"
