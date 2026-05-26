"""Test fixtures for the invoice-aging fixture."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_ROOT = HERE.parent
sys.path.insert(0, str(FIXTURE_ROOT))
