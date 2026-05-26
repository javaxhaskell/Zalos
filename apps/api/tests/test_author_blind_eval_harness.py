"""Blind evaluation harness smoke tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_blind_eval_cases_present() -> None:
    cases_root = _REPO_ROOT / "blind_eval_cases"
    case_dirs = sorted(path for path in cases_root.iterdir() if (path / "case.json").is_file())
    assert len(case_dirs) >= 5
    for case_dir in case_dirs:
        meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        assert "expected_status" in meta
        assert (case_dir / meta["input_file"]).is_file()
        assert (case_dir / meta["prompt_file"]).is_file()


def test_blind_eval_harness_passes() -> None:
    script = _REPO_ROOT / "apps" / "api" / "scripts" / "run_author_blind_eval.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
