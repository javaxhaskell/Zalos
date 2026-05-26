"""Print the live FastAPI OpenAPI schema to stdout.

Usage:
    cd apps/api && uv run python scripts/snapshot_openapi.py > openapi.snapshot.json

CI diffs the committed snapshot against the live `/openapi.json` to catch
unintentional contract drift. Schema changes should be paired with an ADR
and a deliberate snapshot regeneration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Self-bootstrap: ensure ``src`` is on sys.path so this script works
# regardless of how it's invoked (uv run, plain python, CI step).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agentforge.api.main import create_app  # noqa: E402


def main() -> int:
    app = create_app()
    schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
