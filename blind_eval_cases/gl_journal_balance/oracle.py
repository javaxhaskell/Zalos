"""Independent oracle — GL journal case requires model-backed custom generation."""

from __future__ import annotations

from pathlib import Path


def run_oracle(*, workspace: Path, input_path: Path) -> tuple[bool, str]:
    del workspace, input_path
    return False, "GL journal validation is not a validated deterministic scaffold."
