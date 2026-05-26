"""Eval runner orchestrator (BP10b).

Walks the bundled scenarios, drives each through the real
:class:`AuthorFlow` / :class:`RepairFlow` (via :func:`spawn_flow_task`'s
internal `_run_flow` would be over-the-top here — we run synchronously
inline so the runner returns the full ``EvalRunSummary`` to the
caller), captures pass/fail + latency, and persists rows to
``eval_runs`` + ``eval_results``.

Why inline-async rather than the BP8 background-task supervisor:
the eval endpoint is intentionally synchronous-feeling — it returns
once every scenario has finished so the UI can render a stable
summary. The background-task path is for user-driven session runs
where polling-and-go is the right shape. The runner still runs each
scenario's flow on the same event loop the request handler is on, so
``app.state.model_client`` swap-in lands cleanly.

Author scenarios use the configured model client. Repair scenarios use
scripted fixture responses because Repair evals score the deterministic
repair evidence gate, not new Author artifact creation. The
``EvalRunRow`` row is new per invocation (one per call).
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session as DBSession

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.agent.prompts import load_author_prompt, load_repair_prompt
from agentforge.config import Settings
from agentforge.evals.scenarios import (
    is_adversarial,
    is_author,
    load_all_scenarios,
    resolve_repo_path,
)
from agentforge.evals.scripts import invoice_aging_script
from agentforge.models import (
    FakeModelClient,
    ModelClient,
    ModelMessage,
    TextBlock,
)
from agentforge.orchestrator.author_flow import AuthorFlow
from agentforge.orchestrator.repair_flow import RepairFlow
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.models import (
    EvalResultRow,
    EvalRunRow,
)
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    AdversarialScenario,
    ActorType,
    AuthorScenario,
    EvalRunResult,
    EvalRunSummary,
    EvalScenario,
    EventKind,
    RepairScenario,
    Workflow,
)
from agentforge.tools import ToolRegistry, build_registry

_logger = logging.getLogger("agentforge.evals.runner")


class EvalRunner:
    """Walks every scenario through the orchestrator + persists results.

    The runner is stateless across invocations — every call to
    :meth:`run_all` produces a fresh ``EvalRunRow``. The DB session +
    workspace manager + settings are passed in by the caller (route
    or test), so the runner stays decoupled from FastAPI's DI graph.
    """

    def __init__(
        self,
        *,
        db: DBSession,
        workspace_manager: WorkspaceManager,
        settings: Settings,
        registry: ToolRegistry | None = None,
        author_model_client: ModelClient | None = None,
    ) -> None:
        self.db = db
        self.wm = workspace_manager
        self.settings = settings
        self.registry = registry or build_registry()
        self.author_model_client = author_model_client

    async def run_all(
        self,
        scenarios: list[EvalScenario] | None = None,
    ) -> EvalRunSummary:
        """Run every scenario; return + persist the aggregated summary."""
        scenarios = scenarios if scenarios is not None else load_all_scenarios()
        run_id = uuid4()
        started = datetime.now(UTC)
        results: list[EvalRunResult] = []
        for scenario in scenarios:
            try:
                result = await self._run_one(scenario)
            except Exception as exc:  # noqa: BLE001 — supervisor must absorb
                _logger.exception(
                    "scenario raised",
                    extra={"scenario_id": scenario.id},
                )
                result = EvalRunResult(
                    scenario_id=scenario.id,
                    passed=False,
                    latency_ms=0,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)

        completed = datetime.now(UTC)
        summary = self._build_summary(
            run_id=run_id,
            started_at=started,
            completed_at=completed,
            scenarios=scenarios,
            results=results,
        )
        self._persist(summary, results)
        return summary

    # ------------------------------------------------------------------
    # Per-scenario dispatch
    # ------------------------------------------------------------------

    async def _run_one(self, scenario: EvalScenario) -> EvalRunResult:
        """Allocate a fresh session, stage inputs, run the flow."""
        t0 = time.monotonic()
        session_id = uuid4()
        workflow = (
            Workflow.AUTHOR if (is_author(scenario) or is_adversarial(scenario)) else Workflow.REPAIR
        )
        self.wm.allocate(
            session_id=session_id, workflow=workflow, started_at=datetime.now(UTC)
        )
        event_log = EventLog(self.wm)
        idem = IdempotencyStore(db=self.db)

        if is_author(scenario):
            assert isinstance(scenario, AuthorScenario)
            self._stage_author_inputs(session_id, scenario)
            if self.author_model_client is None:
                return EvalRunResult(
                    scenario_id=scenario.id,
                    passed=False,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    failure_reason="author_eval_requires_configured_model_client",
                )
            model_client = self.author_model_client
            initial_messages = [
                ModelMessage(
                    role="user",
                    content=[
                        TextBlock(
                            text=(
                                "<user_message>\n"
                                f"{scenario.workflow_description}\n"
                                "</user_message>"
                            )
                        )
                    ],
                )
            ]
            self._record_author_prompt(
                event_log=event_log,
                session_id=session_id,
                workflow_description=scenario.workflow_description,
            )
            loop = AgentLoop(
                registry=self.registry,
                model_client=model_client,
                idempotency_store=idem,
                event_log=event_log,
                workspace_manager=self.wm,
                settings=self.settings,
            )
            flow = AuthorFlow(
                agent_loop=loop,
                event_log=event_log,
                workspace_manager=self.wm,
                settings=self.settings,
            )
            author_outcome = await flow.run(
                session_id=session_id,
                system_prompt=load_author_prompt(),
                initial_messages=initial_messages,
                budgets=self._budgets(workflow),
            )
            terminal = author_outcome.terminal_status

        elif is_adversarial(scenario):
            assert isinstance(scenario, AdversarialScenario)
            self._stage_adversarial_inputs(session_id, scenario)
            if self.author_model_client is None:
                return EvalRunResult(
                    scenario_id=scenario.id,
                    passed=False,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    failure_reason="author_eval_requires_configured_model_client",
                )
            model_client = self.author_model_client
            initial_messages = [
                ModelMessage(
                    role="user",
                    content=[
                        TextBlock(
                            text=(
                                "<user_message>\n"
                                f"{scenario.workflow_description}\n"
                                "</user_message>"
                            )
                        )
                    ],
                )
            ]
            self._record_author_prompt(
                event_log=event_log,
                session_id=session_id,
                workflow_description=scenario.workflow_description,
            )
            loop = AgentLoop(
                registry=self.registry,
                model_client=model_client,
                idempotency_store=idem,
                event_log=event_log,
                workspace_manager=self.wm,
                settings=self.settings,
            )
            flow = AuthorFlow(
                agent_loop=loop,
                event_log=event_log,
                workspace_manager=self.wm,
                settings=self.settings,
            )
            outcome = await flow.run(
                session_id=session_id,
                system_prompt=load_author_prompt(),
                initial_messages=initial_messages,
                budgets=self._budgets(workflow),
            )
            terminal = outcome.terminal_status

        else:
            assert isinstance(scenario, RepairScenario)
            self._stage_repair_fixture(session_id, scenario)
            script = invoice_aging_script(session_id=session_id)
            model_client = FakeModelClient(script=script)
            problem_text = self._read_problem_report(scenario)
            initial_messages = [
                ModelMessage(
                    role="user",
                    content=[
                        TextBlock(
                            text=(
                                "<problem_report>\n"
                                f"{problem_text}\n"
                                "</problem_report>"
                            )
                        )
                    ],
                )
            ]
            loop = AgentLoop(
                registry=self.registry,
                model_client=model_client,
                idempotency_store=idem,
                event_log=event_log,
                workspace_manager=self.wm,
                settings=self.settings,
            )
            repair_flow = RepairFlow(
                agent_loop=loop,
                event_log=event_log,
                workspace_manager=self.wm,
                settings=self.settings,
            )
            repair_outcome = await repair_flow.run(
                session_id=session_id,
                system_prompt=load_repair_prompt(),
                initial_messages=initial_messages,
                budgets=self._budgets(workflow),
            )
            terminal = repair_outcome.terminal_status

        latency = int((time.monotonic() - t0) * 1000)
        passed = terminal == scenario.expected_terminal_status
        reason: str | None = None
        if not passed:
            reason = (
                f"terminal_status={terminal.value}, "
                f"expected={scenario.expected_terminal_status.value}"
            )
        return EvalRunResult(
            scenario_id=scenario.id,
            passed=passed,
            latency_ms=latency,
            failure_reason=reason,
        )

    # ------------------------------------------------------------------
    # Input staging — copies repo-relative inputs into the session workspace
    # ------------------------------------------------------------------

    def _stage_author_inputs(
        self, session_id: UUID, scenario: AuthorScenario
    ) -> UUID:
        """Copy ``input_files`` into ``uploads/``; stage golden into ``evals/``."""
        workspace = self.wm.get(session_id)
        uploads = workspace / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        for repo_rel in scenario.input_files:
            src = resolve_repo_path(repo_rel)
            shutil.copy2(src, uploads / "sample_input.csv")
        evals_dir = workspace / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)
        for spec in scenario.expected_output_files:
            shutil.copy2(
                resolve_repo_path(spec.golden),
                evals_dir / "golden_output.csv",
            )
        return uuid4()

    def _record_author_prompt(
        self,
        *,
        event_log: EventLog,
        session_id: UUID,
        workflow_description: str,
    ) -> None:
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.USER,
            payload={
                "kind": "author_user_workflow",
                "text": workflow_description,
            },
            step=0,
        )

    def _stage_adversarial_inputs(
        self, session_id: UUID, scenario: AdversarialScenario
    ) -> UUID:
        """Copy the adversarial input + the bank-categoriser golden."""
        workspace = self.wm.get(session_id)
        uploads = workspace / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        # ADV-01's input lives in evals/fixtures/ADV-01/sample_input.csv.
        injection_input = resolve_repo_path(
            "evals/fixtures/ADV-01/sample_input.csv"
        )
        shutil.copy2(injection_input, uploads / "sample_input.csv")
        evals_dir = workspace / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)
        # ADV-01's golden is the bank-categoriser golden (the scenario
        # asserts the agent stays on the rules cascade despite the
        # injection — same output as A-01).
        shutil.copy2(
            resolve_repo_path("evals/golden/bank_categoriser/golden_output.csv"),
            evals_dir / "golden_output.csv",
        )
        return uuid4()

    def _stage_repair_fixture(
        self, session_id: UUID, scenario: RepairScenario
    ) -> None:
        """Copy the broken-agent fixture into ``working/`` + stage expected_output."""
        workspace = self.wm.get(session_id)
        working = workspace / "working"
        working.mkdir(parents=True, exist_ok=True)
        fixture_root = resolve_repo_path(scenario.fixture)
        for item in fixture_root.iterdir():
            if item.name in ("README.md", "__pycache__", ".pytest_cache"):
                continue
            dest = working / item.name
            if item.is_dir():
                shutil.copytree(
                    item, dest, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                shutil.copy2(item, dest)
        evals_dir = workspace / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)
        expected = fixture_root / "data" / "expected_output.csv"
        if expected.is_file():
            shutil.copy2(expected, evals_dir / "expected_output.csv")

    def _read_problem_report(self, scenario: RepairScenario) -> str:
        path = resolve_repo_path(scenario.problem_report_path)
        if not path.is_file():
            return "(problem report missing)"
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _budgets(self, workflow: Workflow) -> LoopBudgets:
        if workflow == Workflow.AUTHOR:
            return LoopBudgets(
                max_steps=self.settings.budget_steps_author,
                max_tokens=self.settings.budget_tokens,
                max_wall_seconds=self.settings.budget_wall_seconds,
            )
        return LoopBudgets(
            max_steps=self.settings.budget_steps_repair,
            max_tokens=self.settings.budget_tokens,
            max_wall_seconds=self.settings.budget_wall_seconds,
        )

    def _build_summary(
        self,
        *,
        run_id: UUID,
        started_at: datetime,
        completed_at: datetime,
        scenarios: list[EvalScenario],
        results: list[EvalRunResult],
    ) -> EvalRunSummary:
        per_tag: dict[str, dict[str, int]] = {}
        for scenario, result in zip(scenarios, results, strict=True):
            tag = scenario.kind.value
            bucket = per_tag.setdefault(tag, {"passed": 0, "failed": 0})
            bucket["passed" if result.passed else "failed"] += 1
        passed = sum(1 for r in results if r.passed)
        return EvalRunSummary(
            id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            per_tag=per_tag,
            results=results,
        )

    def _persist(
        self,
        summary: EvalRunSummary,
        results: list[EvalRunResult],
    ) -> None:
        run_row = EvalRunRow(
            id=str(summary.id),
            started_at=summary.started_at,
            completed_at=summary.completed_at,
            total=summary.total,
            passed=summary.passed,
            failed=summary.failed,
        )
        self.db.add(run_row)
        for result in results:
            self.db.add(
                EvalResultRow(
                    id=str(uuid4()),
                    run_id=str(summary.id),
                    scenario_id=result.scenario_id,
                    passed=result.passed,
                    latency_ms=result.latency_ms,
                    cost_usd=result.cost_usd,
                    failure_reason=result.failure_reason,
                    diff_path=result.diff_path,
                )
            )
        self.db.commit()


async def run_all_scenarios(
    *,
    db: DBSession,
    workspace_manager: WorkspaceManager,
    settings: Settings,
    author_model_client: ModelClient | None = None,
) -> EvalRunSummary:
    """One-shot helper used by the HTTP endpoint + tests."""
    runner = EvalRunner(
        db=db,
        workspace_manager=workspace_manager,
        settings=settings,
        author_model_client=author_model_client,
    )
    return await runner.run_all()


__all__ = ["EvalRunner", "run_all_scenarios"]


# Suppress an unused-import warning for Path — kept for type clarity in
# future extensions (resolve_repo_path returns Path).
_ = Path
