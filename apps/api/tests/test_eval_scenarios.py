"""Validate committed eval scenarios against the canonical Pydantic schemas.

This guards against scenario-schema drift: any change to the EvalScenario
contract that breaks a committed scenario will fail CI.

Note: scenarios are loaded via ``model_validate_json`` because the JSON-input
path is the canonical ingestion route (the eval runner reads .json files).
``model_validate(dict)`` is stricter under Pydantic v2 strict mode and would
reject string→enum coercion that JSON-input correctly handles.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentforge.schemas import (
    AdversarialScenario,
    AuthorScenario,
    EvalKind,
    RepairScenario,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "evals" / "scenarios"


def _load_scenarios() -> list[Path]:
    if not SCENARIOS_DIR.is_dir():
        pytest.skip(f"scenarios dir not found: {SCENARIOS_DIR}")
    paths = sorted(SCENARIOS_DIR.glob("*.json"))
    assert paths, f"no scenarios committed under {SCENARIOS_DIR}"
    return paths


@pytest.mark.parametrize("path", _load_scenarios(), ids=lambda p: p.name)
def test_scenario_validates_against_schema(path: Path) -> None:
    raw = path.read_text()
    data = json.loads(raw)
    kind = data.get("kind")
    if kind == EvalKind.AUTHOR.value:
        AuthorScenario.model_validate_json(raw)
    elif kind == EvalKind.REPAIR.value:
        RepairScenario.model_validate_json(raw)
    elif kind == EvalKind.ADVERSARIAL.value:
        AdversarialScenario.model_validate_json(raw)
    else:
        pytest.fail(f"unknown scenario kind {kind!r} in {path}")


def test_minimum_three_scenarios_committed() -> None:
    """Phase 2 commits exactly the three baseline scenarios (1 author + 1 repair + 1 adversarial)."""
    paths = _load_scenarios()
    ids = {p.stem for p in paths}
    expected = {"A-01_bank_categoriser", "R-01_invoice_aging", "ADV-01_csv_injection"}
    assert expected.issubset(ids), (
        f"missing scenarios: {expected - ids}; have: {ids}"
    )


def test_one_scenario_of_each_kind() -> None:
    """Eval coverage: at least one author, one repair, one adversarial scenario."""
    kinds: set[str] = set()
    for path in _load_scenarios():
        data = json.loads(path.read_text())
        kinds.add(data["kind"])
    assert kinds >= {"author", "repair", "adversarial"}, (
        f"eval coverage missing a kind: {kinds}"
    )
