"""Reference coercion helpers and prompt scaffolding for generated finance agents."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_STRING_ARITHMETIC_TYPEERROR = re.compile(
    r"unsupported operand type\(s\) for .+: 'str' and 'str'",
    re.IGNORECASE,
)


def is_missing_numeric_coercion_typeerror(failure_detail: str) -> bool:
    """Return True when execution failed because arithmetic used raw strings."""
    return bool(_STRING_ARITHMETIC_TYPEERROR.search(failure_detail or ""))


def safe_decimal(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    """Parse finance numeric cell values safely for generated-agent arithmetic."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        for symbol in ("$", "£", "€", "USD", "GBP", "EUR"):
            text = text.replace(symbol, "")
        text = text.replace(",", "").strip()
        if not text:
            return default
        try:
            return Decimal(text)
        except InvalidOperation:
            return default
    return default


def safe_string(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def parse_date(value: Any, *, default: date | None = None) -> date | None:
    """Parse common CSV/XLSX date strings for aging and date comparisons."""
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return default


def generated_agent_coercion_scaffold() -> str:
    """Python helper scaffold generated agents should include before business logic."""
    return '''\
def safe_decimal(value, default=None):
    """Coerce CSV/XLSX numeric cells before arithmetic."""
    from decimal import Decimal, InvalidOperation
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        for symbol in ("$", "£", "€", "USD", "GBP", "EUR"):
            text = text.replace(symbol, "")
        text = text.replace(",", "").strip()
        if not text:
            return default
        try:
            return Decimal(text)
        except InvalidOperation:
            return default
    return default


def parse_date(value, default=None):
    """Coerce CSV/XLSX date cells before aging or date comparisons."""
    from datetime import date, datetime
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return default
'''


def codegen_data_coercion_guidance() -> dict[str, Any]:
    return {
        "planning_rules": [
            "Never perform arithmetic directly on raw CSV/XLSX cell values.",
            "Before arithmetic, coerce relevant fields with a robust numeric parser such as safe_decimal.",
            "Before aging buckets or date comparisons, coerce date fields with parse_date or an equivalent helper.",
            "Treat blank numeric fields as zero only when contract semantics allow it; otherwise flag the row for review or exit with a clear validation error.",
            "Generated code must not crash with TypeError from subtracting or comparing raw strings.",
            "Include helper functions for numeric and date coercion near the top of generated/agent.py.",
        ],
        "repair_rule": (
            "When execution fails with TypeError unsupported operand type(s) for -: 'str' and 'str', "
            "add safe_decimal/parse_date helpers and coerce every arithmetic or date operand from "
            "input rows before business logic. Do not patch only one column name unless that is the "
            "only operand in the failing expression."
        ),
        "required_helpers": ["safe_decimal", "parse_date"],
        "scaffold": generated_agent_coercion_scaffold(),
    }


def codegen_data_coercion_section() -> str:
    guidance = codegen_data_coercion_guidance()
    lines = [
        "Input type coercion requirements:",
        "- Never perform arithmetic directly on raw CSV/XLSX cell values.",
        "- Before arithmetic, coerce operands with safe_decimal or an equivalent helper.",
        "- Before aging buckets or date comparisons, coerce date fields with parse_date.",
        "- Handle blank, None, comma-formatted numbers, and simple currency symbols safely.",
        "- Do not subtract or compare raw row[...] string values without coercion.",
        "- Fail with a clear error or contract-backed review flag rather than TypeError.",
        "Include helper functions similar to this scaffold near the top of generated/agent.py:",
        guidance["scaffold"],
    ]
    return "\n".join(lines)


def data_coercion_repair_requirements() -> list[str]:
    return [
        "Execution failed because arithmetic used raw string values from CSV/XLSX input.",
        "Add safe_decimal and parse_date helpers (or equivalent) near the top of generated/agent.py.",
        "Coerce every numeric operand used in calculated_fields formulas before arithmetic.",
        "Coerce every date operand used for aging buckets or date comparisons before use.",
        "Do not patch only one hardcoded column name; normalize the operands involved in the failing expression.",
        "Preserve the existing CLI interface and required output paths.",
        "Do not catch all exceptions and write empty outputs; fix the root type coercion issue.",
    ]
