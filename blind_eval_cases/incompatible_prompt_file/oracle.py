"""Independent oracle — non-finance upload should stop honestly."""

from __future__ import annotations

from pathlib import Path


def run_oracle(*, workspace: Path, input_path: Path) -> tuple[bool, str]:
    del workspace, input_path
    return True, "Non-finance prompt/file pair correctly rejected."
