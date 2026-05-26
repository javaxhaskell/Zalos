"""Eval-runner package (BP10b).

Walks the JSON scenarios in ``evals/scenarios/`` through the real
orchestrator + agent loop, against scripted ``FakeModelClient``
instances. Persists results to ``eval_runs`` + ``eval_results`` and
exposes them via ``GET /evals/latest``.
"""

from __future__ import annotations

from agentforge.evals.runner import EvalRunner, run_all_scenarios
from agentforge.evals.scenarios import load_all_scenarios

__all__ = ["EvalRunner", "load_all_scenarios", "run_all_scenarios"]
