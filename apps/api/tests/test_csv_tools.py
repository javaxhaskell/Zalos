"""Unit tests for ``inspect_csv_schema`` and ``inspect_xlsx_schema`` (BP4).

The two profilers must:

  * Return canonical :class:`FileProfile` shape carrying the caller-
    supplied ``file_id`` so downstream phases can link the profile back
    to the upload event.
  * Report per-column dtype (pandas-native + regex-based date detection
    for object columns) and ``null_rate``.
  * Surface ``ambiguity_note`` exactly when the spec's heuristic fires —
    a 3-part dash/slash date column where every part is ≤ 12. 4-digit-
    year columns are NOT ambiguous because the year position contains a
    value > 12.
  * Cap profiling at ``max_rows_profiled`` while reporting the file's
    actual ``row_count``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pytest

from agentforge.tools.csv_tools import (
    InspectCsvInput,
    InspectXlsxInput,
    inspect_csv_schema_handler,
    inspect_xlsx_schema_handler,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_SAMPLE = _REPO_ROOT / "templates/bank_categoriser/data/sample_input.csv"
_INVOICE_SAMPLE = (
    _REPO_ROOT
    / "fixtures/broken_agents/invoice_aging_v1/data/sample_input.csv"
)


def _ws(tool_ctx: Any) -> Path:
    return tool_ctx.workspace_manager.get(tool_ctx.session_id)


def _copy_into_uploads(ctx: Any, src: Path, dest_name: str | None = None) -> str:
    dest = _ws(ctx) / "uploads" / (dest_name or src.name)
    shutil.copy(src, dest)
    return f"uploads/{dest.name}"


# ---------------------------------------------------------------------------
# CSV — bank categoriser sample (well-typed, ISO dates)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _BANK_SAMPLE.exists(), reason="bank_categoriser sample not present"
)
async def test_inspect_csv_schema_on_bank_categoriser_sample(tool_ctx: Any) -> None:
    rel = _copy_into_uploads(tool_ctx, _BANK_SAMPLE)
    file_id = uuid4()

    profile = await inspect_csv_schema_handler(
        InspectCsvInput(path=rel, file_id=file_id), tool_ctx
    )

    assert profile.file_id == file_id
    assert profile.filename == "sample_input.csv"
    assert profile.row_count == 200
    column_names = [c.name for c in profile.columns]
    assert column_names == [
        "txn_id",
        "date",
        "amount",
        "description",
        "counterparty",
        "account",
    ]
    by_name = {c.name: c for c in profile.columns}
    assert by_name["amount"].dtype == "float64"
    # ISO dates "2026-02-14" → first part is the year (4-digit > 12) so
    # the spec heuristic returns no ambiguity note.
    assert by_name["date"].dtype == "date"
    assert by_name["date"].ambiguity_note is None
    # 'counterparty' has empty values in the sample → non-zero null rate.
    assert 0.0 < by_name["counterparty"].null_rate <= 1.0


@pytest.mark.skipif(
    not _INVOICE_SAMPLE.exists(),
    reason="invoice_aging fixture sample not present",
)
async def test_inspect_csv_schema_invoice_aging_dates_unambiguous(
    tool_ctx: Any,
) -> None:
    """Sample contains rows like ``03-21-2026`` — position-2 value 21 > 12
    disambiguates the format, so the heuristic must NOT flag it as
    ambiguous. The bug demonstrated by the fixture (DD-MM vs MM-DD
    misparse) is downstream of profiling.
    """
    rel = _copy_into_uploads(tool_ctx, _INVOICE_SAMPLE)
    profile = await inspect_csv_schema_handler(
        InspectCsvInput(path=rel, file_id=uuid4()), tool_ctx
    )
    by_name = {c.name: c for c in profile.columns}
    assert by_name["invoice_date"].dtype == "date"
    assert by_name["invoice_date"].ambiguity_note is None


async def test_inspect_csv_schema_flags_ambiguous_two_digit_year(
    tool_ctx: Any,
) -> None:
    """Synthetic 2-digit-year column where every part ≤ 12 → ambiguous."""
    rel = "uploads/ambig.csv"
    body = (
        "txn_id,short_date\n"
        "T-1,01-02-08\n"
        "T-2,03-04-09\n"
        "T-3,05-06-10\n"
        "T-4,07-08-11\n"
        "T-5,09-10-12\n"
    )
    (_ws(tool_ctx) / "uploads" / "ambig.csv").write_text(body)

    profile = await inspect_csv_schema_handler(
        InspectCsvInput(path=rel, file_id=uuid4()), tool_ctx
    )
    by_name = {c.name: c for c in profile.columns}
    assert by_name["short_date"].dtype == "date"
    assert by_name["short_date"].ambiguity_note is not None
    assert "ambiguous" in by_name["short_date"].ambiguity_note.lower()


async def test_inspect_csv_schema_caps_at_max_rows_profiled(tool_ctx: Any) -> None:
    rel = "uploads/wide.csv"
    rows = ["a,b"] + [f"{i},{i * 2}" for i in range(1, 21)]
    (_ws(tool_ctx) / "uploads" / "wide.csv").write_text("\n".join(rows))

    profile = await inspect_csv_schema_handler(
        InspectCsvInput(path=rel, file_id=uuid4(), max_rows_profiled=5),
        tool_ctx,
    )
    # row_count reflects the actual file, not the profile cap.
    assert profile.row_count == 20
    # 5 distinct sample values come back even though profile capped at 5.
    by_name = {c.name: c for c in profile.columns}
    assert len(by_name["a"].sample_values) == 5


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


async def test_inspect_xlsx_schema_default_sheet(tool_ctx: Any) -> None:
    df = pd.DataFrame(
        {
            "txn_id": ["T-1", "T-2", "T-3"],
            "amount": [10.0, 20.5, 30.25],
            "approved": [True, False, True],
        }
    )
    path = _ws(tool_ctx) / "uploads" / "book.xlsx"
    df.to_excel(path, sheet_name="Transactions", index=False)

    profile = await inspect_xlsx_schema_handler(
        InspectXlsxInput(path="uploads/book.xlsx", file_id=uuid4()),
        tool_ctx,
    )
    assert profile.sheet_name == "Transactions"
    assert profile.row_count == 3
    by_name = {c.name: c for c in profile.columns}
    assert by_name["amount"].dtype == "float64"
    assert by_name["approved"].dtype == "bool"


async def test_inspect_xlsx_schema_explicit_sheet(tool_ctx: Any) -> None:
    path = _ws(tool_ctx) / "uploads" / "multi.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame({"x": [1, 2]}).to_excel(writer, sheet_name="First", index=False)
        pd.DataFrame({"y": ["a", "b", "c"]}).to_excel(
            writer, sheet_name="Second", index=False
        )

    profile = await inspect_xlsx_schema_handler(
        InspectXlsxInput(path="uploads/multi.xlsx", file_id=uuid4(), sheet="Second"),
        tool_ctx,
    )
    assert profile.sheet_name == "Second"
    assert profile.row_count == 3
    assert [c.name for c in profile.columns] == ["y"]
