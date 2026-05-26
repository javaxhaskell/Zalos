"""Row-aligned golden-output comparison (Layer 5 of ADR-0007).

The golden engine treats one CSV as the reference and another as the
candidate. Both are loaded as dicts keyed on the supplied
``primary_key`` (e.g., ``txn_id``). Per-column comparison tolerates
floating-point drift up to ``float_tolerance`` (default 1e-6).

Outputs a :class:`GoldenDiffResult` describing every divergence:
missing rows, extra rows, mismatched cells. The validator layer
function reduces this to a single :class:`ValidationCheck` for the
final :class:`ValidationReport`.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

_ID_LIKE_PRIMARY_KEY_COLUMNS: tuple[str, ...] = (
    "expense_id",
    "invoice_id",
    "transaction_id",
    "txn_id",
    "payment_id",
    "charge_id",
    "merchant_id",
    "settlement_id",
    "payout_id",
    "row_id",
    "id",
)


@dataclass
class CellMismatch:
    primary_key: str
    column: str
    actual: str
    expected: str


@dataclass
class GoldenDiffResult:
    matched: bool
    row_count_actual: int
    row_count_golden: int
    missing_in_actual: list[str] = field(default_factory=list)
    """Primary keys present in golden but absent from actual."""
    extra_in_actual: list[str] = field(default_factory=list)
    cell_mismatches: list[CellMismatch] = field(default_factory=list)

    @property
    def total_divergences(self) -> int:
        return (
            len(self.missing_in_actual)
            + len(self.extra_in_actual)
            + len(self.cell_mismatches)
        )

    def summary(self, limit: int = 10) -> str:
        if self.matched:
            return f"{self.row_count_actual}/{self.row_count_golden} rows matched golden"
        parts: list[str] = []
        if self.missing_in_actual:
            parts.append(f"{len(self.missing_in_actual)} rows missing")
        if self.extra_in_actual:
            parts.append(f"{len(self.extra_in_actual)} extra rows")
        if self.cell_mismatches:
            sample = "; ".join(
                f"{m.primary_key}.{m.column}={m.actual!r}≠{m.expected!r}"
                for m in self.cell_mismatches[:limit]
            )
            parts.append(f"{len(self.cell_mismatches)} cell mismatches ({sample})")
        return ", ".join(parts)


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", newline="") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def resolve_golden_primary_key(
    *,
    actual_csv: Path,
    golden_csv: Path,
    contract_primary_row_key: str | None = None,
    golden_config_primary_key: str | None = None,
) -> str:
    """Pick the row-alignment column for golden comparison.

    Priority:
    1. ``contract_primary_row_key`` when present in both CSV headers
    2. ``golden_config_primary_key`` when present in both CSV headers
    3. First shared ID-like column (``expense_id``, ``invoice_id``, …)
    4. Raise :class:`GoldenDiffError` with a user-facing message
    """
    actual_headers = _read_csv_header(actual_csv)
    golden_headers = _read_csv_header(golden_csv)
    actual_set = set(actual_headers)
    golden_set = set(golden_headers)
    shared = actual_set & golden_set

    if contract_primary_row_key:
        if contract_primary_row_key in shared:
            return contract_primary_row_key
        raise GoldenDiffError(
            "Golden comparison could not align rows: contract primary_row_key "
            f"{contract_primary_row_key!r} is missing from the actual output, "
            "golden expected output, or both. Ensure both files share the same "
            "row identifier column."
        )

    if golden_config_primary_key:
        if golden_config_primary_key in shared:
            return golden_config_primary_key
        raise GoldenDiffError(
            "Golden comparison could not align rows: configured golden primary key "
            f"{golden_config_primary_key!r} is missing from the actual output, "
            "golden expected output, or both."
        )

    for column in _ID_LIKE_PRIMARY_KEY_COLUMNS:
        if column in shared:
            return column

    raise GoldenDiffError(
        "Golden comparison could not align rows because the actual output and "
        "golden expected output share no row identifier column. Add a stable "
        "ID column (for example expense_id or transaction_id) to both files."
    )


def golden_diff(
    *,
    actual_csv: Path,
    golden_csv: Path,
    primary_key: str,
    float_tolerance: float = 1e-6,
    ignore_columns: tuple[str, ...] = (),
) -> GoldenDiffResult:
    """Compare ``actual_csv`` against ``golden_csv`` row-by-row.

    Both files must have the ``primary_key`` column. ``ignore_columns``
    lists column names skipped during comparison (e.g., timestamps the
    agent inserts that the golden doesn't track).
    """
    actual_rows = _read_csv(actual_csv)
    golden_rows = _read_csv(golden_csv)

    actual_by_pk: dict[str, dict[str, str]] = {}
    for row in actual_rows:
        if primary_key not in row:
            raise GoldenDiffError(
                f"actual row missing primary_key {primary_key!r}: {row}"
            )
        actual_by_pk[row[primary_key]] = row

    golden_by_pk: dict[str, dict[str, str]] = {}
    for row in golden_rows:
        if primary_key not in row:
            raise GoldenDiffError(
                f"golden row missing primary_key {primary_key!r}: {row}"
            )
        golden_by_pk[row[primary_key]] = row

    missing = sorted(set(golden_by_pk) - set(actual_by_pk))
    extra = sorted(set(actual_by_pk) - set(golden_by_pk))
    common = sorted(set(actual_by_pk) & set(golden_by_pk))

    cell_mismatches: list[CellMismatch] = []
    ignored = set(ignore_columns)
    for pk in common:
        a = actual_by_pk[pk]
        g = golden_by_pk[pk]
        for col in g:
            if col in ignored:
                continue
            if col not in a:
                cell_mismatches.append(
                    CellMismatch(primary_key=pk, column=col, actual="<missing>", expected=g[col])
                )
                continue
            if not _values_match(a[col], g[col], float_tolerance):
                cell_mismatches.append(
                    CellMismatch(primary_key=pk, column=col, actual=a[col], expected=g[col])
                )

    matched = not missing and not extra and not cell_mismatches
    return GoldenDiffResult(
        matched=matched,
        row_count_actual=len(actual_rows),
        row_count_golden=len(golden_rows),
        missing_in_actual=missing,
        extra_in_actual=extra,
        cell_mismatches=cell_mismatches,
    )


class GoldenDiffError(RuntimeError):
    """Raised when the inputs cannot be compared (missing PK, malformed CSV)."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _values_match(actual: str, expected: str, tol: float) -> bool:
    """Compare two CSV cells; floats within ``tol`` tie."""
    if actual == expected:
        return True
    try:
        af = float(actual)
        ef = float(expected)
    except ValueError:
        return False
    if math.isnan(af) and math.isnan(ef):
        return True
    return abs(af - ef) <= tol


__all__ = [
    "CellMismatch",
    "GoldenDiffError",
    "GoldenDiffResult",
    "golden_diff",
    "resolve_golden_primary_key",
]
