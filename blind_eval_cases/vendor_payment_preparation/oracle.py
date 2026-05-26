"""Independent oracle — vendor payment case requires a model call before any completion."""

from __future__ import annotations

from pathlib import Path


def run_oracle(*, workspace: Path, input_path: Path) -> tuple[bool, str]:
    del workspace, input_path
    return False, "Vendor payment preparation is not a validated deterministic scaffold."
