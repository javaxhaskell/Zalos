"""Read tools that profile CSV and XLSX files into a typed ``FileProfile``.

Both tools cap the profiling cost at ``max_rows_profiled`` (default 50_000
per ADR-0010) and return the canonical
:class:`agentforge.schemas.workflow.FileProfile` so downstream phases
(``author_infer``, ``repair_triage``) consume one consistent shape.

The ``file_id`` is taken as an input rather than looked up server-side
because the canonical ``FileProfile.file_id`` is required and the
inspector should not need a database handle. The agent obtains the
``file_id`` from the ``FILE_UPLOADED`` event payload (see
:class:`FileUploadResponse`) and threads it into the inspect call —
this is the same action/observation pattern OpenHands uses for chaining
tool outputs.

Date-format ambiguity heuristic (per HANDOFF.md §4): a column is
flagged ambiguous iff every value matches the 3-part dash/slash
pattern *and* no part exceeds 12 (in which case neither DD-MM nor
MM-DD can be ruled out by the data). 4-digit-year columns are
unambiguous because the year position contains a value > 12.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from agentforge.schemas import (
    ColumnProfile,
    FileProfile,
    RiskLevel,
    StrictModel,
    ToolDefinition,
    ToolPhase,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"
_INFO_PHASES: list[ToolPhase] = [ToolPhase.AUTHOR_INFO, ToolPhase.REPAIR_INFO]

_DATE_3PART = re.compile(r"^\d{1,4}[-/]\d{1,4}[-/]\d{1,4}$")
_SAMPLE_SIZE = 5
"""How many distinct values to include in ``ColumnProfile.sample_values``."""
_AMBIGUITY_PROBE = 200
"""How many non-null values to scan when checking date-format ambiguity."""


# ---------------------------------------------------------------------------
# inspect_csv_schema
# ---------------------------------------------------------------------------


class InspectCsvInput(StrictModel):
    """Arguments for ``inspect_csv_schema``."""

    path: str
    file_id: UUID
    """The ``UploadedFile.id`` for this file. Threaded from the
    ``FILE_UPLOADED`` event payload (``uploaded_file_id``)."""
    max_rows_profiled: int = 50_000


async def inspect_csv_schema_handler(
    args: InspectCsvInput, ctx: ToolContext
) -> FileProfile:
    """Profile up to ``max_rows_profiled`` rows of a workspace-relative CSV."""
    import pandas as pd

    resolved = ctx.workspace_manager.resolve_in(ctx.session_id, args.path)
    if not resolved.is_file():
        raise FileNotFoundError(f"not a file: {args.path}")

    df = pd.read_csv(resolved, nrows=args.max_rows_profiled, low_memory=False)
    row_count = _count_csv_data_rows(resolved)

    return FileProfile(
        file_id=args.file_id,
        filename=resolved.name,
        row_count=row_count,
        encoding="utf-8",
        columns=[_profile_column(name, df[name]) for name in df.columns],
        sheet_name=None,
    )


INSPECT_CSV_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="inspect_csv_schema",
        description=(
            "Profile a workspace-relative CSV file. Returns per-column "
            "dtype, null rate, up to 5 sample values, and an ambiguity "
            "note when date-format detection is ambiguous. file_id is "
            "the uploaded_file_id from the FILE_UPLOADED event."
        ),
        input_schema_name="InspectCsvInput",
        output_schema_name="FileProfile",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=InspectCsvInput,
    output_schema=FileProfile,
    handler=inspect_csv_schema_handler,
)


# ---------------------------------------------------------------------------
# inspect_xlsx_schema
# ---------------------------------------------------------------------------


class InspectXlsxInput(StrictModel):
    """Arguments for ``inspect_xlsx_schema``."""

    path: str
    file_id: UUID
    sheet: str | None = None
    """When ``None`` the first sheet is profiled."""
    max_rows_profiled: int = 50_000


async def inspect_xlsx_schema_handler(
    args: InspectXlsxInput, ctx: ToolContext
) -> FileProfile:
    """Profile up to ``max_rows_profiled`` rows of a workspace-relative XLSX."""
    import pandas as pd

    resolved = ctx.workspace_manager.resolve_in(ctx.session_id, args.path)
    if not resolved.is_file():
        raise FileNotFoundError(f"not a file: {args.path}")

    sheet_name: str | int = args.sheet if args.sheet is not None else 0
    df = pd.read_excel(
        resolved,
        sheet_name=sheet_name,
        nrows=args.max_rows_profiled,
        engine="openpyxl",
    )

    # When the caller passed an explicit sheet name we trust it; otherwise
    # surface the first sheet's actual name by opening the workbook.
    resolved_sheet_name: str
    if args.sheet is not None:
        resolved_sheet_name = args.sheet
    else:
        from openpyxl import load_workbook

        wb = load_workbook(filename=resolved, read_only=True)
        resolved_sheet_name = wb.sheetnames[0]
        wb.close()

    # For XLSX, row_count is the profiled length (openpyxl streaming the
    # entire workbook just to count rows is wasteful for the prototype).
    row_count = int(df.shape[0])

    return FileProfile(
        file_id=args.file_id,
        filename=resolved.name,
        row_count=row_count,
        encoding="utf-8",
        columns=[_profile_column(name, df[name]) for name in df.columns],
        sheet_name=resolved_sheet_name,
    )


INSPECT_XLSX_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="inspect_xlsx_schema",
        description=(
            "Profile a workspace-relative XLSX file (first sheet by "
            "default). Returns per-column dtype, null rate, up to 5 "
            "sample values, and an ambiguity note for ambiguous date "
            "columns. file_id is the uploaded_file_id from the "
            "FILE_UPLOADED event."
        ),
        input_schema_name="InspectXlsxInput",
        output_schema_name="FileProfile",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASES,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=InspectXlsxInput,
    output_schema=FileProfile,
    handler=inspect_xlsx_schema_handler,
)


# ---------------------------------------------------------------------------
# Profiling helpers
# ---------------------------------------------------------------------------


def _profile_column(name: str, series: pd.Series) -> ColumnProfile:
    """Build a :class:`ColumnProfile` from one pandas Series."""
    null_count = int(series.isna().sum())
    total = int(len(series))
    null_rate = (null_count / total) if total else 0.0

    non_null_str = series.dropna().astype(str)
    sample_values = _distinct_head(non_null_str.tolist(), _SAMPLE_SIZE)
    probe = non_null_str.head(_AMBIGUITY_PROBE).tolist()

    dtype, ambiguity_note = _infer_dtype_and_ambiguity(series, probe)

    return ColumnProfile(
        name=str(name),
        dtype=dtype,
        null_rate=round(null_rate, 4),
        sample_values=sample_values,
        ambiguity_note=ambiguity_note,
    )


def _distinct_head(values: list[str], n: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= n:
            break
    return out


def _infer_dtype_and_ambiguity(
    series: pd.Series, probe: list[str]
) -> tuple[str, str | None]:
    """Return ``(dtype, ambiguity_note)``.

    pandas-native numeric/datetime/bool dtypes are honoured first; object
    columns get a regex-based date detection so date-shaped strings are
    surfaced as ``dtype='date'`` (with an ambiguity note when the format
    cannot be determined from the data).
    """
    kind = series.dtype.kind
    if kind in ("i", "u"):
        return "int64", None
    if kind == "f":
        return "float64", None
    if kind == "b":
        return "bool", None
    if kind == "M":
        return "date", None

    # object — could be string or date-shaped string
    if probe and all(_DATE_3PART.match(v) for v in probe):
        return "date", _date_ambiguity_note(probe)
    return "string", None


def _date_ambiguity_note(values: list[str]) -> str | None:
    """Spec heuristic: if every 3-part date has all parts <= 12, flag ambiguous.

    Any value with a part > 12 disambiguates the format (because months
    only run 1..12), so we return ``None``. 4-digit-year values always
    contain a part > 12, so any modern ISO/MDY/DMY column with full
    years is unambiguous by this rule.
    """
    for value in values:
        parts = re.split(r"[-/]", value)
        if len(parts) != 3:
            return None
        try:
            ints = [int(p) for p in parts]
        except ValueError:
            return None
        if any(p > 12 for p in ints):
            return None
    return "date format ambiguous: could be DD-MM-YYYY or MM-DD-YYYY"


def _count_csv_data_rows(path: Path) -> int:
    """Count newline-delimited rows in a CSV minus one for the header.

    Synthetic finance data does not embed newlines inside quoted cells,
    so a line count is correct. Production swap would use
    :func:`csv.reader` to handle multi-line cells.
    """
    with path.open("rb") as f:
        total = sum(1 for _ in f)
    return max(total - 1, 0)
