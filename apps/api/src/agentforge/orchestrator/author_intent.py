"""Author workflow intent vs uploaded-schema alignment helpers.

These helpers may stop clearly incompatible uploads, but they must not choose
workflow business logic or route successful runs through deterministic
templates. Tabular Author builds are handled by the LLM-first pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

DetectedFileType = Literal[
    "bank_transactions",
    "invoices",
    "payment_processor_reconciliation",
    "unknown",
]
RequestedWorkflow = Literal[
    "bank_categorisation",
    "invoice_aging",
    "other_finance",
    "unspecified",
]

_BANK_TRANSACTION_COLUMNS = frozenset(
    {"txn_id", "date", "amount", "description", "counterparty", "account"}
)
_INVOICE_COLUMN_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"invoice_id", "customer", "due_date", "amount", "status"}),
    frozenset({"invoice_id", "customer", "invoice_date", "amount", "status"}),
)
_INVOICE_MISSING_LABELS = (
    "invoice_id",
    "customer",
    "invoice_date or due_date",
    "status",
)

_INVOICE_AGING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"invoice\s+aging", re.I),
    re.compile(r"aging\s+bucket", re.I),
    re.compile(r"days?\s+overdue", re.I),
    re.compile(r"due_date", re.I),
    re.compile(r"overdue\s+invoice", re.I),
    re.compile(r"high-?risk\s+overdue", re.I),
)
_BANK_CATEG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"bank\s+transaction", re.I),
    re.compile(r"categoris", re.I),
    re.compile(r"transaction\s+categor", re.I),
    re.compile(r"category\s+assignment", re.I),
    re.compile(r"\b(income|office expense|travel|subscriptions|refund|uncategorised)\b", re.I),
)
_OTHER_FINANCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"vendor\s+payment", re.I),
    re.compile(r"payment\s+processor", re.I),
    re.compile(r"payment\s+reconciliation", re.I),
    re.compile(r"processor\s+reconciliation", re.I),
    re.compile(r"\bgl\b", re.I),
    re.compile(r"general\s+ledger", re.I),
    re.compile(r"journal\s+entr", re.I),
)
_PAYMENT_RECON_COLUMN_SIGNALS = frozenset(
    {
        "processor",
        "settlement_date",
        "merchant_id",
        "gross_amount",
        "fee",
        "fee_amount",
        "net_amount",
        "settlement_batch",
        "payout_id",
        "transaction_id",
    }
)
VALIDATED_AUTHOR_TEMPLATE = "bank_categoriser"
UploadFormat = Literal["csv", "xlsx"]
AuthorPrePipelineGate = Literal[
    "proceed",
    "intent_mismatch",
    "custom_build",
    "unsupported",
]


@dataclass(frozen=True)
class IntentSchemaAssessment:
    """Result of comparing user intent, template, and upload schema."""

    aligned: bool
    needs_clarification: bool = False
    detected_file_type: DetectedFileType = "unknown"
    requested_workflow: RequestedWorkflow = "unspecified"
    missing_columns: list[str] = field(default_factory=list)
    detected_columns: list[str] = field(default_factory=list)
    explanation: str = ""
    next_steps: str = ""
    clarification_question: str = ""


def normalise_columns(columns: list[str]) -> set[str]:
    return {c.strip().lower() for c in columns if c.strip()}


def classify_uploaded_schema(columns: list[str]) -> DetectedFileType:
    cols = normalise_columns(columns)
    if cols >= _BANK_TRANSACTION_COLUMNS or (
        "txn_id" in cols and "account" in cols and "amount" in cols
    ):
        return "bank_transactions"
    if "invoice_id" in cols:
        return "invoices"
    if cols & {"due_date", "invoice_date"} and cols & {"amount", "customer"}:
        return "invoices"
    if "processor" in cols or len(cols & _PAYMENT_RECON_COLUMN_SIGNALS) >= 2:
        return "payment_processor_reconciliation"
    return "unknown"


def classify_requested_workflow(description: str) -> RequestedWorkflow:
    text = description.strip()
    if not text:
        return "unspecified"
    if any(p.search(text) for p in _INVOICE_AGING_PATTERNS):
        return "invoice_aging"
    if any(p.search(text) for p in _OTHER_FINANCE_PATTERNS):
        return "other_finance"
    if any(p.search(text) for p in _BANK_CATEG_PATTERNS):
        return "bank_categorisation"
    return "unspecified"


def missing_invoice_columns(columns: list[str]) -> list[str]:
    cols = normalise_columns(columns)
    if any(group <= cols for group in _INVOICE_COLUMN_GROUPS):
        return []
    missing: list[str] = []
    for label in _INVOICE_MISSING_LABELS:
        if label == "invoice_date or due_date":
            if not ({"invoice_date", "due_date"} & cols):
                missing.append(label)
            continue
        if label not in cols:
            missing.append(label)
    return missing


def assess_intent_schema_alignment(
    *,
    user_description: str,
    template_name: str,
    csv_columns: list[str],
) -> IntentSchemaAssessment:
    """Assess whether a selected reference template conflicts with the upload."""
    file_type = classify_uploaded_schema(csv_columns)
    requested = classify_requested_workflow(user_description)
    cols = normalise_columns(csv_columns)

    if template_name != "bank_categoriser":
        return IntentSchemaAssessment(aligned=True)

    missing_bank = sorted(_BANK_TRANSACTION_COLUMNS - cols)
    if missing_bank and file_type == "bank_transactions":
        return IntentSchemaAssessment(
            aligned=False,
            detected_file_type=file_type,
            requested_workflow=requested,
            missing_columns=missing_bank,
            explanation=(
                "The uploaded CSV is missing columns required for bank "
                "transaction categorisation."
            ),
            next_steps=(
                "Upload a bank-transaction CSV with txn_id, date, amount, "
                "description, counterparty, and account, or change the "
                "workflow description."
            ),
        )

    if file_type == "bank_transactions" and requested == "invoice_aging":
        return IntentSchemaAssessment(
            aligned=False,
            detected_file_type=file_type,
            requested_workflow=requested,
            missing_columns=list(_INVOICE_MISSING_LABELS),
            explanation=(
                "The uploaded file looks like bank transactions, but the "
                "requested workflow looks like invoice aging. The file has an "
                "amount column, but it is missing invoice-specific fields "
                "required for invoice aging."
            ),
            next_steps=(
                "Upload an invoice CSV, or change the workflow to bank "
                "transaction categorisation."
            ),
        )

    if file_type == "bank_transactions" and requested == "other_finance":
        return IntentSchemaAssessment(
            aligned=False,
            detected_file_type=file_type,
            requested_workflow=requested,
            missing_columns=list(_INVOICE_MISSING_LABELS),
            explanation=(
                "The uploaded file looks like bank transactions, but the "
                "requested workflow describes a different finance process."
            ),
            next_steps=(
                "Upload input that matches the workflow you described, or "
                "change the workflow to bank transaction categorisation."
            ),
        )

    if file_type == "bank_transactions" and requested in {
        "bank_categorisation",
        "unspecified",
    }:
        return IntentSchemaAssessment(
            aligned=True,
            detected_file_type=file_type,
            requested_workflow=requested,
        )

    if requested == "bank_categorisation" and file_type != "bank_transactions":
        missing = missing_invoice_columns(csv_columns) or missing_bank
        return IntentSchemaAssessment(
            aligned=False,
            detected_file_type=file_type,
            requested_workflow=requested,
            missing_columns=missing or list(_BANK_TRANSACTION_COLUMNS),
            explanation=(
                "The workflow description asks for bank transaction "
                "categorisation, but the uploaded file does not look like "
                "a bank-transaction CSV."
            ),
            next_steps=(
                "Upload a bank-transaction CSV with txn_id, date, amount, "
                "description, counterparty, and account."
            ),
        )

    if requested == "invoice_aging" and file_type != "invoices":
        return IntentSchemaAssessment(
            aligned=False,
            detected_file_type=file_type,
            requested_workflow=requested,
            missing_columns=list(_INVOICE_MISSING_LABELS),
            explanation=(
                "The requested workflow looks like invoice aging, but the "
                "uploaded file does not include invoice columns."
            ),
            next_steps=(
                "Upload an invoice CSV with invoice_id, customer, "
                "invoice_date or due_date, amount, and status."
            ),
        )

    if (
        file_type == "bank_transactions"
        and requested == "unspecified"
        and _mentions_invoice_and_bank_ambiguity(user_description)
    ):
        return IntentSchemaAssessment(
            aligned=False,
            needs_clarification=True,
            detected_file_type=file_type,
            requested_workflow="invoice_aging",
            explanation=(
                "We detected bank transaction columns, but your description "
                "also mentions invoice aging."
            ),
            clarification_question=(
                "We detected bank transaction columns, but your description "
                "sounds like invoice aging. Which workflow should we build?"
            ),
            next_steps=(
                "Reply with bank transaction categorisation or invoice aging, "
                "and upload a matching CSV."
            ),
        )

    return IntentSchemaAssessment(
        aligned=True,
        detected_file_type=file_type,
        requested_workflow=requested,
    )


def _is_custom_author_build(template_name: str | None) -> bool:
    return not template_name


def _allows_experimental_custom_build(
    *,
    upload_format: UploadFormat,
    columns: list[str],
    user_description: str,
) -> bool:
    """Custom build may proceed only for bank-shaped CSV uploads."""
    if upload_format != "csv":
        return False
    file_type = classify_uploaded_schema(columns)
    requested = classify_requested_workflow(user_description)
    return file_type == "bank_transactions" and requested in {
        "bank_categorisation",
        "unspecified",
    }


def _workflow_label(
    requested: RequestedWorkflow, user_description: str
) -> str:
    text = user_description.lower()
    if requested == "other_finance" and (
        "payment processor" in text or "processor reconciliation" in text
    ):
        return "payment processor reconciliation"
    if requested == "other_finance":
        return "custom finance workflow"
    if requested == "invoice_aging":
        return "invoice aging"
    if requested == "bank_categorisation":
        return "bank transaction categorisation"
    return "custom workflow"


def _detected_file_label(
    *,
    file_type: DetectedFileType,
    upload_format: UploadFormat,
    upload_filename: str,
) -> str:
    if file_type == "payment_processor_reconciliation":
        return "payment processor reconciliation spreadsheet"
    if file_type == "bank_transactions":
        return "bank transaction file"
    if file_type == "invoices":
        return "invoice file"
    if upload_format == "xlsx":
        return "spreadsheet upload"
    lowered = upload_filename.lower()
    if "reconciliation" in lowered or "processor" in lowered:
        return "payment processor reconciliation spreadsheet"
    return "uploaded file"


def build_custom_workflow_not_validated_assessment(
    *,
    user_description: str,
    template_name: str | None,
    columns: list[str],
    upload_format: UploadFormat,
    upload_filename: str = "",
) -> IntentSchemaAssessment:
    requested = classify_requested_workflow(user_description)
    file_type = classify_uploaded_schema(columns)
    workflow_label = _workflow_label(requested, user_description)
    file_label = _detected_file_label(
        file_type=file_type,
        upload_format=upload_format,
        upload_filename=upload_filename,
    )
    return IntentSchemaAssessment(
        aligned=False,
        detected_file_type=file_type,
        requested_workflow=requested,
        detected_columns=list(columns),
        explanation=(
            f"The uploaded file appears to be a {file_label}, but the "
            "system could not establish enough tabular context for LLM-first "
            "authoring."
        ),
        next_steps=(
            f"Clarify the {workflow_label} request or upload a compatible "
            "finance CSV/XLSX file."
        ),
    )


def assess_author_pre_pipeline(
    *,
    user_description: str,
    template_name: str | None,
    columns: list[str],
    upload_format: UploadFormat,
    upload_filename: str = "",
) -> tuple[AuthorPrePipelineGate, IntentSchemaAssessment | None]:
    """Decide which Author path may run before INFO or BUILD."""
    file_type = classify_uploaded_schema(columns)
    requested = classify_requested_workflow(user_description)
    if requested == "invoice_aging" and file_type == "bank_transactions":
        return (
            "intent_mismatch",
            assess_intent_schema_alignment(
                user_description=user_description,
                template_name=VALIDATED_AUTHOR_TEMPLATE,
                csv_columns=columns,
            ),
        )
    if requested == "bank_categorisation" and file_type == "invoices":
        return (
            "intent_mismatch",
            IntentSchemaAssessment(
                aligned=False,
                detected_file_type=file_type,
                requested_workflow=requested,
                detected_columns=list(columns),
                explanation=(
                    "The uploaded file appears to contain invoices, but the prompt asks "
                    "for bank transaction categorisation."
                ),
                next_steps="Upload bank transactions or change the workflow description.",
            ),
        )
    if upload_format in {"csv", "xlsx"} and columns and user_description.strip():
        return ("custom_build", None)

    if template_name == VALIDATED_AUTHOR_TEMPLATE and upload_format == "csv":
        intent = assess_intent_schema_alignment(
            user_description=user_description,
            template_name=template_name,
            csv_columns=columns,
        )
        if not intent.aligned:
            return ("intent_mismatch", intent)

    if _is_custom_author_build(template_name):
        if _allows_experimental_custom_build(
            upload_format=upload_format,
            columns=columns,
            user_description=user_description,
        ):
            return ("proceed", None)

    return (
        "unsupported",
        build_custom_workflow_not_validated_assessment(
            user_description=user_description,
            template_name=template_name,
            columns=columns,
            upload_format=upload_format,
            upload_filename=upload_filename,
        ),
    )


def _mentions_invoice_and_bank_ambiguity(description: str) -> bool:
    text = description.lower()
    has_invoice = "invoice" in text or "aging" in text
    has_bank = "bank" in text or "transaction" in text
    return has_invoice and has_bank and classify_requested_workflow(description) == "unspecified"


def read_csv_header(path) -> list[str]:
    import csv
    from pathlib import Path

    csv_path = Path(path)
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            return next(reader)
        except StopIteration:
            return []


def read_xlsx_header(path) -> list[str]:
    from pathlib import Path

    import openpyxl

    workbook = openpyxl.load_workbook(Path(path), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        if sheet is None:
            return []
        row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        if row is None:
            return []
        return [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
    finally:
        workbook.close()


def read_upload_header(path) -> tuple[list[str], UploadFormat]:
    from pathlib import Path

    upload_path = Path(path)
    suffix = upload_path.suffix.lower()
    if suffix == ".csv":
        return read_csv_header(upload_path), "csv"
    if suffix in {".xlsx", ".xls"}:
        return read_xlsx_header(upload_path), "xlsx"
    return [], "csv"


__all__ = [
    "AuthorPrePipelineGate",
    "IntentSchemaAssessment",
    "UploadFormat",
    "VALIDATED_AUTHOR_TEMPLATE",
    "assess_author_pre_pipeline",
    "assess_intent_schema_alignment",
    "build_custom_workflow_not_validated_assessment",
    "classify_requested_workflow",
    "classify_uploaded_schema",
    "normalise_columns",
    "read_csv_header",
    "read_upload_header",
    "read_xlsx_header",
]
