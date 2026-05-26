"""Tests for the BP10b eval runner + HTTP endpoints.

Covers:

  * ``EvalRunner.run_all`` against the three bundled scenarios
    (A-01 bank_categoriser, R-01 invoice_aging, ADV-01 csv_injection).
    Tests inject scripted Author model responses explicitly; production
    eval routes use the configured model client.
  * ``EvalRunner.run_all`` persists one ``EvalRunRow`` + N
    ``EvalResultRow`` per invocation. INV-7: running it twice produces
    consistent per-scenario results.
  * ``POST /evals/run`` returns 201 with the summary; ``GET /evals/latest``
    returns the most-recent run; 404 before any run exists.
  * ``scenarios.load_all_scenarios`` round-trips through the discriminated
    union without losing the scenario-kind discriminator.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from agentforge.config import get_settings
from agentforge.evals.runner import EvalRunner
from agentforge.evals.scenarios import (
    is_adversarial,
    is_author,
    is_repair,
    load_all_scenarios,
)
from agentforge.models import FakeModelClient, ModelResponse
from agentforge.persistence.db import get_session_factory
from agentforge.persistence.models import EvalResultRow, EvalRunRow
from agentforge.persistence.workspace import WorkspaceManager
from tests.author_model_fixtures import model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"

# ---------------------------------------------------------------------------
# scenarios loader
# ---------------------------------------------------------------------------


def test_load_all_scenarios_returns_three_typed_entries() -> None:
    scenarios = load_all_scenarios()
    assert len(scenarios) == 3
    ids = [s.id for s in scenarios]
    assert ids == sorted(ids)  # filename order
    assert any(is_author(s) for s in scenarios)
    assert any(is_repair(s) for s in scenarios)
    assert any(is_adversarial(s) for s in scenarios)
    # Discriminated union didn't drop the kind tag.
    assert {s.kind.value for s in scenarios} == {
        "author",
        "repair",
        "adversarial",
    }


# ---------------------------------------------------------------------------
# Runner — direct in-process invocation
# ---------------------------------------------------------------------------


def _runner_factory(
    db: DBSession,
    workspaces_root,  # noqa: ANN001 — fixture
    *,
    author_script_repeats: int = 2,
) -> EvalRunner:
    wm = WorkspaceManager(root=workspaces_root)
    settings = get_settings()
    return EvalRunner(
        db=db,
        workspace_manager=wm,
        settings=settings,
        author_model_client=FakeModelClient(
            script=_author_eval_script(repeats=author_script_repeats)
        ),
    )


def _author_eval_script(*, repeats: int) -> list[ModelResponse]:
    script: list[ModelResponse] = []
    for _ in range(repeats):
        script.extend(
            model_authoring_responses(
                template_root=_BANK_DIR,
                workflow_type="bank_transaction_categorisation",
            )
        )
    return script


def _install_author_eval_model(app_client: TestClient, *, repeats: int = 2) -> None:
    app_client.app.state.model_client = FakeModelClient(
        script=_author_eval_script(repeats=repeats)
    )


async def test_eval_runner_walks_all_three_scenarios_to_completed(
    app_client: TestClient,  # ensures schema is created + engine wired
    workspaces_root,  # noqa: ANN001
) -> None:
    """All three scenarios complete and the persisted rows reflect it."""
    factory = get_session_factory()
    db = factory()
    try:
        runner = _runner_factory(db, workspaces_root)
        summary = await runner.run_all()

        assert summary.total == 3
        assert summary.passed == 3, (
            f"failures: {[r for r in summary.results if not r.passed]}"
        )
        assert summary.failed == 0
        # Per-tag breakdown reflects the three kinds.
        assert summary.per_tag["author"]["passed"] == 1
        assert summary.per_tag["repair"]["passed"] == 1
        assert summary.per_tag["adversarial"]["passed"] == 1
        # Each result records a positive latency.
        for result in summary.results:
            assert result.latency_ms >= 0
            assert result.passed is True
            assert result.failure_reason is None

        # Persistence: one EvalRunRow + three EvalResultRow.
        run_rows = db.query(EvalRunRow).all()
        assert len(run_rows) == 1
        assert run_rows[0].passed == 3 and run_rows[0].failed == 0
        result_rows = (
            db.query(EvalResultRow)
            .filter(EvalResultRow.run_id == run_rows[0].id)
            .all()
        )
        assert len(result_rows) == 3
        assert {r.scenario_id for r in result_rows} == {
            "A-01_bank_categoriser",
            "R-01_invoice_aging",
            "ADV-01_csv_injection",
        }
    finally:
        db.close()


async def test_eval_runner_is_idempotent_per_scenario(
    app_client: TestClient,
    workspaces_root,  # noqa: ANN001
) -> None:
    """Running the suite twice produces the same per-scenario pass/fail.

    The latency_ms varies wall-to-wall, but the typed outcome shouldn't:
    scripted model responses + bundled fixtures + deterministic
    orchestrator = same `passed` per `scenario_id` across runs.
    """
    factory = get_session_factory()
    db = factory()
    try:
        runner = _runner_factory(db, workspaces_root, author_script_repeats=4)
        first = await runner.run_all()
        second = await runner.run_all()

        first_by_id = {r.scenario_id: r.passed for r in first.results}
        second_by_id = {r.scenario_id: r.passed for r in second.results}
        assert first_by_id == second_by_id
        # Two distinct EvalRunRow rows.
        assert db.query(EvalRunRow).count() == 2
        # Six EvalResultRow rows total (3 per run).
        assert db.query(EvalResultRow).count() == 6
    finally:
        db.close()


# ---------------------------------------------------------------------------
# HTTP — POST /evals/run + GET /evals/latest
# ---------------------------------------------------------------------------


def test_post_evals_run_returns_201_with_summary(
    app_client: TestClient,
) -> None:
    _install_author_eval_model(app_client)
    resp = app_client.post("/evals/run")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["passed"] == 3
    assert body["failed"] == 0
    assert {r["scenario_id"] for r in body["results"]} == {
        "A-01_bank_categoriser",
        "R-01_invoice_aging",
        "ADV-01_csv_injection",
    }
    # Per-tag breakdown surfaces.
    assert body["per_tag"]["author"]["passed"] == 1
    assert body["per_tag"]["repair"]["passed"] == 1
    assert body["per_tag"]["adversarial"]["passed"] == 1


def test_get_evals_latest_404_before_any_run(app_client: TestClient) -> None:
    resp = app_client.get("/evals/latest")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "no_eval_runs"


def test_get_evals_latest_returns_most_recent(app_client: TestClient) -> None:
    _install_author_eval_model(app_client, repeats=4)
    first = app_client.post("/evals/run")
    assert first.status_code == 201
    first_run_id = first.json()["id"]

    second = app_client.post("/evals/run")
    assert second.status_code == 201
    second_run_id = second.json()["id"]
    assert first_run_id != second_run_id  # one row per invocation

    latest = app_client.get("/evals/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["id"] == second_run_id
    assert body["total"] == 3
    # The per-tag breakdown is reconstructed from scenario_id prefixes.
    assert body["per_tag"]["author"]["passed"] == 1
    assert body["per_tag"]["repair"]["passed"] == 1
    assert body["per_tag"]["adversarial"]["passed"] == 1


# ---------------------------------------------------------------------------
# Smoke: a fresh runner with an empty DB doesn't choke on persistence
# ---------------------------------------------------------------------------


def test_evals_run_persists_into_eval_results_table(
    app_client: TestClient,
) -> None:
    _install_author_eval_model(app_client)
    resp = app_client.post("/evals/run")
    assert resp.status_code == 201

    factory = get_session_factory()
    db = factory()
    try:
        runs = db.query(EvalRunRow).all()
        assert len(runs) == 1
        results = db.query(EvalResultRow).all()
        assert len(results) == 3
        # Each row carries the scenario_id + a latency_ms.
        for r in results:
            assert r.scenario_id
            assert r.latency_ms is not None and r.latency_ms >= 0
            assert r.passed is True
    finally:
        db.close()


@pytest.mark.parametrize(
    "scenario_id",
    [
        "A-01_bank_categoriser",
        "R-01_invoice_aging",
        "ADV-01_csv_injection",
    ],
)
def test_evals_run_includes_each_scenario(
    app_client: TestClient, scenario_id: str
) -> None:
    _install_author_eval_model(app_client)
    resp = app_client.post("/evals/run")
    assert resp.status_code == 201
    ids = {r["scenario_id"] for r in resp.json()["results"]}
    assert scenario_id in ids
