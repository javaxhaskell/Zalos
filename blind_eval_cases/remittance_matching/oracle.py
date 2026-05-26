"""Independent oracle — remittance matching must not pass via zero-token deterministic paths."""

from __future__ import annotations

from pathlib import Path


def run_oracle(*, workspace: Path, input_path: Path) -> tuple[bool, str]:
    del workspace, input_path
    return False, "Remittance matching is not a validated deterministic scaffold."
