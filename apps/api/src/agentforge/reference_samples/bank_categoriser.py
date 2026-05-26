"""Bundled bank transaction categorisation reference sample — detection and staging only.

Prompt guidance for the default Author path lives in the UI default workflow prompt
and the user's ``user_message``. Backend helpers here gate dev-only scaffold mode,
golden staging, and post-run validation evidence — not hidden codegen shortcuts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentforge.config import Settings
from agentforge.schemas import AuthorOutputContract

TEMPLATE = "bank_categoriser"
FILENAME = "bank_transaction_categorisation_demo.csv"
GOLDEN_REPO_REL = "apps/web/app/api/reference-samples/bank-categoriser/expected_output.csv"
GOLDEN_WORKSPACE_REL = "evals/golden_output.csv"

COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "date",
    "account",
    "description",
    "counterparty",
    "amount",
    "currency",
    "reference",
    "direction",
)

REQUIRED_OUTPUT_COLUMNS: tuple[str, ...] = (
    "category",
    "rule_matched",
    "rule_used",
    "confidence_score",
    "review_required",
)

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "Revenue",
    "Payroll",
    "Software",
    "Bank Fees",
    "Travel",
    "Rent",
    "Tax",
    "Office Supplies",
    "Other",
)

SCAFFOLD_WARNING = (
    "Bundled reference sample contract scaffold used for demo stability; generated "
    "agent code and tests were still model-authored and validated."
)


def scaffold_enabled(settings: Settings | None) -> bool:
    return bool(settings and settings.author_enable_bank_reference_scaffold)


def is_bundled_sample(*, template_hint: str | None, schema_profile: dict[str, Any]) -> bool:
    if template_hint != TEMPLATE:
        return False
    columns = list(schema_profile.get("columns") or [])
    if columns != list(COLUMNS):
        return False
    if int(schema_profile.get("row_count") or 0) != 18:
        return False
    input_path = str(
        schema_profile.get("normalized_input_path")
        or schema_profile.get("agent_input_path")
        or schema_profile.get("input_file")
        or ""
    )
    if not input_path:
        return False
    return Path(input_path).name == FILENAME


def is_bundled_contract(*, template_hint: str | None, contract: AuthorOutputContract) -> bool:
    if template_hint != TEMPLATE:
        return False
    if list(contract.input_columns) != list(COLUMNS):
        return False
    return Path(contract.input_file).name == FILENAME
