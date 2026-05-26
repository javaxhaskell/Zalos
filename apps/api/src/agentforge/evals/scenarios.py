"""Eval scenario loader (BP10b).

Walks ``evals/scenarios/*.json``, parses each via the Pydantic
discriminated union :data:`EvalScenario`, returns the parsed list in
filename-sort order so the runner produces deterministic output.

Scenario JSONs are repo-relative paths to other repo files (input
files, fixtures, problem reports). The loader returns absolute paths
via :func:`resolve_repo_path` so the runner doesn't have to guess
where the repo root is.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from agentforge.schemas import (
    AdversarialScenario,
    AuthorScenario,
    EvalScenario,
    RepairScenario,
)

# conftest sets WORKSPACES_ROOT etc.; the scenarios live at the repo
# root regardless of pytest cwd. apps/api/src/agentforge/evals/ →
# parents[5] is the repo root (evals/ < agentforge/ < src/ < api/ < apps/ < Zalos/).
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCENARIOS_DIR = _REPO_ROOT / "evals" / "scenarios"


_SCENARIO_ADAPTER: TypeAdapter[EvalScenario] = TypeAdapter(EvalScenario)


def repo_root() -> Path:
    """The resolved repo root the loader uses for input-file resolution."""
    return _REPO_ROOT


def resolve_repo_path(relative: str) -> Path:
    """Resolve a repo-relative path (e.g. ``templates/.../sample.csv``).

    The scenario JSONs store paths relative to the repo root; the
    runner needs absolute paths to copy files into the per-session
    workspace.
    """
    return _REPO_ROOT / relative


def load_all_scenarios(
    scenarios_dir: Path | None = None,
) -> list[EvalScenario]:
    """Load every ``*.json`` under ``scenarios_dir`` (default: built-in).

    Files are sorted by filename so the runner's output is stable for
    snapshot tests + the ``/admin/evals`` page renders in a predictable
    order.
    """
    directory = scenarios_dir or _SCENARIOS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(
            f"scenarios directory not found: {directory}"
        )
    out: list[EvalScenario] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        scenario = _SCENARIO_ADAPTER.validate_python(raw)
        out.append(scenario)
    return out


def is_author(scenario: EvalScenario) -> bool:
    return isinstance(scenario, AuthorScenario)


def is_repair(scenario: EvalScenario) -> bool:
    return isinstance(scenario, RepairScenario)


def is_adversarial(scenario: EvalScenario) -> bool:
    return isinstance(scenario, AdversarialScenario)


__all__ = [
    "is_adversarial",
    "is_author",
    "is_repair",
    "load_all_scenarios",
    "repo_root",
    "resolve_repo_path",
]
