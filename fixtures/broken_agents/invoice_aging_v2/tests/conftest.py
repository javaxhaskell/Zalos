"""Make the fixture's modules importable when pytest runs from this folder."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_ROOT = HERE.parent
sys.path.insert(0, str(FIXTURE_ROOT))
