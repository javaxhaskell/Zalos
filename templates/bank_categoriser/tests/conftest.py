"""Test fixtures for the bank_categoriser template."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the template's modules importable when pytest is run from this dir.
HERE = Path(__file__).resolve().parent
TEMPLATE_ROOT = HERE.parent
sys.path.insert(0, str(TEMPLATE_ROOT))
