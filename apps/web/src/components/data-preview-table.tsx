"use client";

import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import { useMemo, type ReactNode } from "react";
import * as XLSX from "xlsx";

import { TD, TH } from "@/components/ui/table";
import {
  humaniseReviewFieldValue,
  isHumanisedReviewColumn,
  isUnusableExceptionReasonValue,
  resolveReviewRowReason,
  REVIEW_TABLE_COLUMNS,
  REVIEW_TABLE_HEADER_LABELS,
  REVIEW_TABLE_TRAILING_COLUMN_PRIORITY,
} from "@/lib/ux-language";
import { cn } from "@/lib/utils";

export interface ParsedCsv {
  columns: string[];
  rows: Array<Record<string, string>>;
}

export const DEFAULT_PREVIEW_MAX_ROWS = 10;
export const TABLE_MAX_HEIGHT = "min(28rem, 55vh)";

export type PreviewViewMode = "overview" | "preview";

const AMOUNT_COLUMN_RE =
  /^(amount|total|value|price|debit|credit|balance|fee|net|gross|subtotal|tax|payment|paid|due|payment_amount)$/i;
const AMOUNT_COLUMN_PRIORITY = [
  "amount",
  "payment_amount",
  "total",
  "value",
  "price",
  "net",
  "gross",
  "subtotal",
  "payment",
  "paid",
  "due",
  "debit",
  "credit",
  "balance",
  "fee",
  "tax",
] as const;
const CURRENCY_COLUMN_RE = /^currency$/i;
const CATEGORY_COLUMN_RE = /^category(_name)?$/i;
const CATEGORY_COLUMN_PRIORITY = ["category", "category_name"] as const;

/** Short tokens that stay uppercase in finance column headers (e.g. Invoice ID). */
const HEADER_ACRONYMS = new Set([
  "id",
  "uuid",
  "usd",
  "eur",
  "gbp",
  "cad",
  "aud",
  "chf",
  "jpy",
  "api",
  "url",
  "vat",
  "gst",
]);

export interface DisplayColumnPlan {
  columns: string[];
  headerFor: (column: string) => string;
}

export type ExpenseReviewColumnKind =
  | "id"
  | "exception_reason"
  | "confidence"
  | "field";

export interface ExpenseReviewColumn {
  readonly kind: ExpenseReviewColumnKind;
  /** Stable React key */
  readonly key: string;
  /** Underlying CSV column when present */
  readonly sourceColumn: string | null;
}

export interface ExpenseReviewDisplayPlan {
  readonly columns: ExpenseReviewColumn[];
  readonly headerFor: (column: ExpenseReviewColumn) => string;
  readonly resolveCell: (
    row: Record<string, string>,
    column: ExpenseReviewColumn,
  ) => string;
  readonly humaniseAs: (column: ExpenseReviewColumn) => string;
}

/** Review UI columns hidden from the expense queue data grid (Review column covers them). */
const REVIEW_QUEUE_HIDDEN_COLUMNS = new Set([
  "exception_flag",
  "issue_flag",
  "review_flag",
  "review_required",
  "severity",
]);

/** Normalise CSV headers for alias matching (e.g. "Exception Flag" → exception_flag). */
export function normalizeColumnKey(column: string): string {
  return column.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

const EXCEPTION_FLAG_COLUMN_KEYS = new Set([
  "exception_flag",
  "issue_flag",
  "review_flag",
  "flagged",
  "flag",
  "exception",
  "exception_status",
]);

const REVIEW_REQUIRED_COLUMN_KEYS = new Set(["review_required", "needs_review"]);

function findColumnByNormalizedKeys(
  columns: readonly string[],
  keys: ReadonlySet<string>,
): string | null {
  for (const col of columns) {
    if (keys.has(normalizeColumnKey(col))) return col;
  }
  return null;
}

export function resolveExceptionFlagColumn(
  columns: readonly string[],
): string | null {
  return findColumnByNormalizedKeys(columns, EXCEPTION_FLAG_COLUMN_KEYS);
}

export function resolveReviewRequiredColumn(
  columns: readonly string[],
): string | null {
  return findColumnByNormalizedKeys(columns, REVIEW_REQUIRED_COLUMN_KEYS);
}

export function resolveSeverityColumn(columns: readonly string[]): string | null {
  return findColumnByNormalizedKeys(columns, new Set(["severity"]));
}

function parseFalsyCell(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return (
    normalized === "false" ||
    normalized === "no" ||
    normalized === "n" ||
    normalized === "0"
  );
}

/**
 * Severity for the Review column badge. Uses the CSV severity column when
 * present; otherwise applies finance heuristics for flagged rows.
 */
export function deriveRowSeverity(
  row: Record<string, string>,
  columns: readonly string[],
  flagged: boolean,
): string {
  if (!flagged) return "";

  const severityColumn = resolveSeverityColumn(columns);
  if (severityColumn) {
    const raw = (row[severityColumn] ?? "").trim();
    const normalized = raw.toLowerCase();
    if (raw && normalized !== "none") return raw;
  }

  const amountColumn = detectAmountColumn(columns);
  const policyLimitColumn = resolveColumnByNormalizedKey(columns, "policy_limit");
  if (amountColumn && policyLimitColumn) {
    const amount = parseNumericAmount(row[amountColumn] ?? "");
    const limit = parseNumericAmount(row[policyLimitColumn] ?? "");
    if (amount !== null && limit !== null && amount > limit) {
      return "high";
    }
  }

  const approvalColumn = resolveColumnByNormalizedKey(columns, "approval_status");
  if (approvalColumn) {
    const approval = (row[approvalColumn] ?? "").trim().toLowerCase();
    if (
      approval === "pending" ||
      approval === "rejected" ||
      approval === "not_approved" ||
      approval === "unapproved" ||
      parseFalsyCell(approval)
    ) {
      return "high";
    }
  }

  const receiptColumn = resolveColumnByNormalizedKey(columns, "receipt_attached");
  if (receiptColumn) {
    const receipt = (row[receiptColumn] ?? "").trim();
    if (parseFalsyCell(receipt)) {
      return "medium";
    }
  }

  const confidenceColumn = resolveConfidenceColumn(columns);
  if (confidenceColumn) {
    const confidence = parseRowConfidence(row, confidenceColumn);
    if (confidence < DEFAULT_LOW_CONFIDENCE_THRESHOLD) {
      return "medium";
    }
  }

  return "medium";
}

function isReviewQueueHiddenColumn(column: string): boolean {
  return REVIEW_QUEUE_HIDDEN_COLUMNS.has(normalizeColumnKey(column));
}

export function buildDisplayColumnPlan(parsed: ParsedCsv): DisplayColumnPlan {
  const currencyColumn = parsed.columns.find((col) =>
    CURRENCY_COLUMN_RE.test(col),
  );
  const uniformCurrency = currencyColumn
    ? detectUniformCurrency(parsed.rows, currencyColumn)
    : null;
  const hideCurrencyColumn =
    currencyColumn !== undefined && uniformCurrency !== null;

  const columns = hideCurrencyColumn
    ? parsed.columns.filter((col) => col !== currencyColumn)
    : parsed.columns;

  const headerFor = (column: string): string => {
    if (uniformCurrency && isAmountColumn(column)) {
      return formatAmountColumnLabel(column, uniformCurrency);
    }
    return toTitleCaseLabel(column);
  };

  return { columns, headerFor };
}

/** Title Case header for preview tables (alias for display-plan labelling). */
export function formatPreviewHeader(column: string): string {
  const normalized = normalizeColumnKey(column);
  const override = REVIEW_TABLE_HEADER_LABELS[normalized];
  if (override) return override;
  return toTitleCaseLabel(column);
}

function resolveCanonicalSourceColumn(
  columns: readonly string[],
  sources: readonly string[],
  used: ReadonlySet<string>,
): string | null {
  for (const key of sources) {
    const col = resolveColumnByNormalizedKey(columns, key);
    if (col && !used.has(col)) return col;
  }
  return null;
}

function isBlankReviewCellValue(value: string): boolean {
  return isUnusableExceptionReasonValue(value);
}

function isExceptionReasonExcludedColumn(column: string): boolean {
  return isAmountColumn(column);
}

function resolveFirstMeaningfulExceptionReason(
  row: Record<string, string>,
  columns: readonly string[],
  excludeColumns: ReadonlySet<string> = new Set<string>(),
): string {
  for (const key of REVIEW_TABLE_COLUMNS.exceptionReason.sources) {
    const col = resolveColumnByNormalizedKey(columns, key);
    if (!col || excludeColumns.has(col) || isExceptionReasonExcludedColumn(col)) {
      continue;
    }
    const raw = (row[col] ?? "").trim();
    if (!isBlankReviewCellValue(raw)) return raw;
  }
  return "";
}

const EXCEPTION_REASON_SYNTHESIS_CONTEXT_KEYS = [
  "merchant",
  "customer",
  "vendor",
  "vendor_name",
  "counterparty",
] as const;

const EXCEPTION_REASON_SYNTHESIS_SIGNAL_KEYS = [
  "rule_used",
  "rule_matched",
  "status",
  "aging_bucket",
  "approval_status",
  "risk_flag",
  "issue_flag",
] as const;

function synthesizeExceptionReasonFromRow(
  row: Record<string, string>,
  columns: readonly string[],
): string {
  for (const key of EXCEPTION_REASON_SYNTHESIS_SIGNAL_KEYS) {
    const column = resolveColumnByNormalizedKey(columns, key);
    if (!column || isExceptionReasonExcludedColumn(column)) continue;
    const raw = (row[column] ?? "").trim();
    if (!raw || isUnusableExceptionReasonValue(raw)) continue;
    const humanised = humaniseReviewFieldValue(key, raw);
    if (humanised.display && humanised.display !== "—") {
      return humanised.display;
    }
  }

  const contextParts: string[] = [];
  for (const key of EXCEPTION_REASON_SYNTHESIS_CONTEXT_KEYS) {
    const column = resolveColumnByNormalizedKey(columns, key);
    if (!column) continue;
    const value = (row[column] ?? "").trim();
    if (value) {
      contextParts.push(value);
      break;
    }
  }

  return contextParts.join(" · ");
}

function resolveExceptionReasonCell(
  row: Record<string, string>,
  columns: readonly string[],
): string {
  if (!isRowFlaggedForReview(row, columns)) {
    return "";
  }

  const fromSources = resolveFirstMeaningfulExceptionReason(row, columns);
  if (fromSources) return fromSources;

  const reason = resolveReviewRowReason(row, columns);
  if (reason.display) return reason.display;

  const synthesized = synthesizeExceptionReasonFromRow(row, columns);
  if (synthesized) return synthesized;

  return "Review required";
}

export function buildExpenseReviewDisplayColumnPlan(
  parsed: ParsedCsv,
): ExpenseReviewDisplayPlan {
  const base = buildDisplayColumnPlan(parsed);
  const available = base.columns.filter((col) => !isReviewQueueHiddenColumn(col));
  const ordered: ExpenseReviewColumn[] = [];
  const used = new Set<string>();

  const addFieldColumn = (col: string | null): void => {
    if (!col || used.has(col) || !available.includes(col)) return;
    ordered.push({ kind: "field", key: col, sourceColumn: col });
    used.add(col);
  };

  const idColumn = resolveCanonicalSourceColumn(
    parsed.columns,
    REVIEW_TABLE_COLUMNS.expenseId.sources,
    used,
  );
  if (idColumn) {
    ordered.push({ kind: "id", key: idColumn, sourceColumn: idColumn });
    used.add(idColumn);
  }

  const exceptionReasonColumn = (() => {
    for (const key of REVIEW_TABLE_COLUMNS.exceptionReason.sources) {
      const col = resolveColumnByNormalizedKey(parsed.columns, key);
      if (!col || used.has(col) || isExceptionReasonExcludedColumn(col)) {
        continue;
      }
      return col;
    }
    return null;
  })();
  ordered.push({
    kind: "exception_reason",
    key: exceptionReasonColumn ?? "__exception_reason__",
    sourceColumn: exceptionReasonColumn,
  });
  if (exceptionReasonColumn) used.add(exceptionReasonColumn);

  const confidenceColumn = resolveCanonicalSourceColumn(
    parsed.columns,
    REVIEW_TABLE_COLUMNS.confidence.sources,
    used,
  );
  if (confidenceColumn) {
    ordered.push({
      kind: "confidence",
      key: confidenceColumn,
      sourceColumn: confidenceColumn,
    });
    used.add(confidenceColumn);
  }

  for (const key of REVIEW_TABLE_TRAILING_COLUMN_PRIORITY) {
    addFieldColumn(resolveColumnByNormalizedKey(available, key));
  }

  const headerFor = (column: ExpenseReviewColumn): string => {
    switch (column.kind) {
      case "id":
        return formatPreviewHeader(column.sourceColumn ?? "expense_id");
      case "exception_reason":
        return REVIEW_TABLE_COLUMNS.exceptionReason.label;
      case "confidence":
        return REVIEW_TABLE_COLUMNS.confidence.label;
      default:
        return column.sourceColumn
          ? base.headerFor(column.sourceColumn)
          : formatPreviewHeader(column.key);
    }
  };

  const resolveCell = (
    row: Record<string, string>,
    column: ExpenseReviewColumn,
  ): string => {
    switch (column.kind) {
      case "exception_reason":
        return resolveExceptionReasonCell(
          row,
          parsed.columns,
        );
      case "confidence":
        return column.sourceColumn ? (row[column.sourceColumn] ?? "") : "";
      default:
        return column.sourceColumn ? (row[column.sourceColumn] ?? "") : "";
    }
  };

  const humaniseAs = (column: ExpenseReviewColumn): string => {
    switch (column.kind) {
      case "exception_reason":
        return "exception_reason";
      case "confidence":
        return column.sourceColumn ?? "confidence";
      default:
        return column.sourceColumn ?? column.key;
    }
  };

  return { columns: ordered, headerFor, resolveCell, humaniseAs };
}

/** Fixed column widths for the expense review queue (table-fixed layout). */
export function expenseReviewColumnWidthClass(
  column: ExpenseReviewColumn,
): string {
  switch (column.kind) {
    case "id":
      return "w-[8rem] min-w-[8rem] max-w-[8rem]";
    case "exception_reason":
      return "w-[12rem] min-w-[12rem] max-w-[12rem]";
    case "confidence":
      return "w-[7rem] min-w-[7rem] max-w-[7rem]";
    default:
      return "w-[8rem] min-w-[8rem] max-w-[8rem]";
  }
}

export function detectUniformCurrency(
  rows: Array<Record<string, string>>,
  currencyColumn: string,
): string | null {
  const values = new Set<string>();
  for (const row of rows) {
    const value = (row[currencyColumn] ?? "").trim();
    if (value) values.add(value);
  }
  if (values.size !== 1) return null;
  return [...values][0] ?? null;
}

export function findCurrencyColumn(columns: readonly string[]): string | null {
  return columns.find((col) => CURRENCY_COLUMN_RE.test(col)) ?? null;
}

export function detectAmountColumn(columns: readonly string[]): string | null {
  const matches = columns.filter((col) => isAmountColumn(col));
  if (matches.length === 0) return null;
  for (const preferred of AMOUNT_COLUMN_PRIORITY) {
    const found = matches.find((col) => col.toLowerCase() === preferred);
    if (found) return found;
  }
  return matches[0] ?? null;
}

export function detectCategoryColumn(columns: readonly string[]): string | null {
  const matches = columns.filter((col) => CATEGORY_COLUMN_RE.test(col));
  if (matches.length === 0) return null;
  for (const preferred of CATEGORY_COLUMN_PRIORITY) {
    const found = matches.find((col) => col.toLowerCase() === preferred);
    if (found) return found;
  }
  return matches[0] ?? null;
}

const BREAKDOWN_DIMENSION_PRIORITY = [
  "category",
  "category_name",
  "aging_bucket",
  "status",
  "risk_flag",
] as const;

/** Best column for overview breakdown charts (category, status, aging bucket, etc.). */
export function detectBreakdownDimensionColumn(
  columns: readonly string[],
): string | null {
  for (const key of BREAKDOWN_DIMENSION_PRIORITY) {
    const column = resolveColumnByNormalizedKey(columns, key);
    if (column) return column;
  }
  return detectCategoryColumn(columns);
}

export function breakdownSectionLabelForColumn(
  column: string | null,
): string {
  if (!column) return "Category breakdown";
  const key = normalizeColumnKey(column);
  if (key === "category" || key === "category_name") {
    return "Category breakdown";
  }
  if (key === "aging_bucket") return "Aging bucket breakdown";
  if (key === "status") return "Status breakdown";
  if (key === "risk_flag") return "Risk breakdown";
  return `${formatPreviewHeader(column)} breakdown`;
}

export const DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.7;

export function isConfidenceColumn(column: string): boolean {
  const key = normalizeColumnKey(column);
  return key === "confidence" || key === "confidence_score";
}

export function resolveConfidenceColumn(
  columns: readonly string[],
): string | null {
  const score = resolveColumnByNormalizedKey(columns, "confidence_score");
  if (score) return score;
  return resolveColumnByNormalizedKey(columns, "confidence");
}

/** Whole-number percentage for confidence_score / confidence cells (e.g. 0.75 → 75%). */
export function formatPreviewConfidence(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  if (trimmed.endsWith("%")) {
    const parsed = Number.parseFloat(trimmed.slice(0, -1).trim());
    if (!Number.isFinite(parsed)) return null;
    return `${Math.round(parsed)}%`;
  }

  const parsed = Number.parseFloat(trimmed);
  if (!Number.isFinite(parsed)) return null;

  const percent = parsed >= 0 && parsed <= 1 ? parsed * 100 : parsed;
  return `${Math.round(percent)}%`;
}

function normalizeFlagValue(value: string): string {
  return value.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

/** Values that mean a row is clean / not flagged (aligned with backend validation). */
const NON_FLAGGED_FLAG_VALUES = new Set([
  "no",
  "no_issue",
  "no_exception",
  "not_flagged",
  "not_required",
  "review_not_required",
  "ok",
  "none",
  "false",
  "0",
  "n",
  "pass",
  "passed",
  "clean",
  "clear",
]);

/** Known enum tokens that mean a row needs finance review. */
const EXPLICIT_FLAGGED_VALUES = new Set([
  "exception",
  "review_required",
  "requires_review",
  "review_needed",
  "needs_review",
  "yes",
  "true",
  "1",
  "y",
  "flagged",
  "flag",
  "fail",
  "failed",
]);

function isFlagColumnValueFlagged(value: string): boolean {
  const flag = normalizeFlagValue(value);
  if (!flag) return false;
  if (NON_FLAGGED_FLAG_VALUES.has(flag)) return false;
  if (EXPLICIT_FLAGGED_VALUES.has(flag)) return true;
  return !NON_FLAGGED_FLAG_VALUES.has(flag);
}

function hasReviewSignalColumns(columns: readonly string[]): boolean {
  return (
    resolveReviewRequiredColumn(columns) !== null ||
    resolveExceptionFlagColumn(columns) !== null ||
    resolveConfidenceColumn(columns) !== null
  );
}

/** Row is flagged for review via exception/issue/review_required columns. */
export function isRowFlaggedForReview(
  row: Record<string, string>,
  columns: readonly string[],
): boolean {
  const exceptionCol = resolveExceptionFlagColumn(columns);
  if (exceptionCol) {
    return isFlagColumnValueFlagged(row[exceptionCol] ?? "");
  }
  const reviewCol = resolveReviewRequiredColumn(columns);
  if (reviewCol) {
    return isFlagColumnValueFlagged(row[reviewCol] ?? "");
  }
  return false;
}

/** Lower-confidence subset that deserves an extra look before sign-off. */
export function rowNeedsExtraAttention(
  row: Record<string, string>,
  columns: readonly string[],
  threshold = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
): boolean {
  const confidenceColumn = resolveConfidenceColumn(columns);
  if (confidenceColumn) {
    const confidence = Number.parseFloat(row[confidenceColumn] ?? "1");
    return Number.isFinite(confidence) && confidence < threshold;
  }
  return isRowFlaggedForReview(row, columns);
}

/** @deprecated Use {@link isRowFlaggedForReview} or {@link rowNeedsExtraAttention}. */
export function rowNeedsReview(
  row: Record<string, string>,
  columns: readonly string[],
  threshold = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
): boolean {
  if (isRowFlaggedForReview(row, columns)) return true;
  const confidenceColumn = resolveConfidenceColumn(columns);
  if (!confidenceColumn) return false;
  const confidence = Number.parseFloat(row[confidenceColumn] ?? "1");
  return Number.isFinite(confidence) && confidence < threshold;
}

function parseRowConfidence(
  row: Record<string, string>,
  confidenceColumn: string | null,
): number {
  if (!confidenceColumn) return 1;
  const confidence = Number.parseFloat(row[confidenceColumn] ?? "1");
  return Number.isFinite(confidence) ? confidence : 1;
}

/**
 * Puts flagged rows first (highest review priority at top), then the rest in
 * original order. Among flagged rows, lower confidence sorts earlier when a
 * confidence column exists.
 */
export function sortRowsForReviewPreview(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
): Array<Record<string, string>> {
  if (!hasReviewSignalColumns(columns)) return rows;
  const confidenceColumn = resolveConfidenceColumn(columns);
  const indexed = rows.map((row, index) => ({ row, index }));
  return indexed
    .sort((a, b) => {
      const aFlagged = isRowFlaggedForReview(a.row, columns);
      const bFlagged = isRowFlaggedForReview(b.row, columns);
      if (aFlagged !== bFlagged) return aFlagged ? -1 : 1;
      if (aFlagged && bFlagged && confidenceColumn) {
        const aConf = parseRowConfidence(a.row, confidenceColumn);
        const bConf = parseRowConfidence(b.row, confidenceColumn);
        if (aConf !== bConf) return aConf - bConf;
      }
      return a.index - b.index;
    })
    .map(({ row }) => row);
}

export function countRowsFlaggedForReview(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
): number | null {
  if (
    resolveReviewRequiredColumn(columns) === null &&
    resolveExceptionFlagColumn(columns) === null
  ) {
    return null;
  }
  return rows.filter((row) => isRowFlaggedForReview(row, columns)).length;
}

export function countRowsNeedingReview(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
  threshold = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
): number | null {
  if (!hasReviewSignalColumns(columns)) return null;
  return rows.filter((row) => rowNeedsReview(row, columns, threshold)).length;
}

export function countRowsNeedingExtraAttention(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
  threshold = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
): number | null {
  if (!resolveConfidenceColumn(columns)) return null;
  return rows.filter((row) => rowNeedsExtraAttention(row, columns, threshold))
    .length;
}

export function parseNumericAmount(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  let normalized = trimmed.replace(/[£$€¥₹]/g, "").replace(/\s+/g, "");
  let negative = false;
  if (/^\(.*\)$/.test(normalized)) {
    negative = true;
    normalized = normalized.slice(1, -1);
  }
  normalized = normalized.replace(/,/g, "");
  if (normalized.startsWith("-")) {
    negative = true;
    normalized = normalized.slice(1);
  } else if (normalized.startsWith("+")) {
    normalized = normalized.slice(1);
  }

  const value = Number.parseFloat(normalized);
  if (!Number.isFinite(value)) return null;
  return negative ? -value : value;
}

export function formatPreviewCurrency(
  amount: number,
  currency: string | null,
): string {
  if (currency) {
    const code = currency.trim().toUpperCase();
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: code,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(amount);
    } catch {
      return `${code} ${amount.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
    }
  }
  return amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export interface AmountBreakdownContext {
  readonly amountColumn: string;
  readonly currency: string | null;
  readonly mixedCurrency: boolean;
}

export function resolveAmountBreakdownContext(
  columns: readonly string[],
  rows: Array<Record<string, string>>,
): AmountBreakdownContext | null {
  const amountColumn = detectAmountColumn(columns);
  if (!amountColumn) return null;

  const hasParseableAmount = rows.some(
    (row) => parseNumericAmount(row[amountColumn] ?? "") !== null,
  );
  if (!hasParseableAmount) return null;

  const currencyColumn = findCurrencyColumn(columns);
  if (!currencyColumn) {
    return { amountColumn, currency: null, mixedCurrency: false };
  }

  const currencyValues = new Set<string>();
  for (const row of rows) {
    const value = (row[currencyColumn] ?? "").trim();
    if (value) currencyValues.add(value);
  }
  if (currencyValues.size <= 1) {
    return {
      amountColumn,
      currency: [...currencyValues][0] ?? null,
      mixedCurrency: false,
    };
  }
  return { amountColumn, currency: null, mixedCurrency: true };
}

function toTitleCaseLabel(column: string): string {
  return column
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase();
      if (HEADER_ACRONYMS.has(lower)) {
        return lower.toUpperCase();
      }
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

function formatAmountColumnLabel(column: string, currency: string): string {
  const base = toTitleCaseLabel(column);
  const code = currency.trim().toUpperCase();
  return code ? `${base} (${code})` : base;
}

function isAmountColumn(column: string): boolean {
  return (
    AMOUNT_COLUMN_RE.test(column) || /amount|total|price|balance/i.test(column)
  );
}

function columnAlignClass(column: string): string {
  return isAmountColumn(column) ? "text-right" : "text-left";
}

export function parseCsv(text: string): ParsedCsv {
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length === 0) return { columns: [], rows: [] };
  const header = lines[0] ?? "";
  const columns = parseRow(header);
  const rows: Array<Record<string, string>> = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = parseRow(lines[i] ?? "");
    const obj: Record<string, string> = {};
    for (let c = 0; c < columns.length; c++) {
      const col = columns[c];
      if (col === undefined) continue;
      obj[col] = cells[c] ?? "";
    }
    rows.push(obj);
  }
  return { columns, rows };
}

function parseRow(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i += 1;
        } else {
          inQuote = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else if (ch === '"' && cur === "") {
      inQuote = true;
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

export function rowsToParsedCsv(
  columns: readonly string[],
  rows: ReadonlyArray<Record<string, string>>,
): ParsedCsv {
  return {
    columns: [...columns],
    rows: rows.map((row) => {
      const obj: Record<string, string> = {};
      for (const col of columns) {
        obj[col] = row[col] ?? "";
      }
      return obj;
    }),
  };
}

export function PreviewSectionLabel({
  children,
  className,
}: {
  readonly children: ReactNode;
  readonly className?: string;
}) {
  return (
    <h4
      className={cn(
        "mb-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500",
        className,
      )}
    >
      {children}
    </h4>
  );
}

export function previewViewTitle(_mode?: PreviewViewMode): string {
  void _mode;
  return "Overview";
}

export function previewTableSectionLabel(showAllRows: boolean): string {
  return showAllRows ? "All rows" : "Rows";
}

export type OutputPreviewRowScope = "all" | "flagged";

export function outputPreviewTableSectionLabel(scope: OutputPreviewRowScope): string {
  return scope === "flagged" ? "Rows requiring review" : "All rows";
}

export function categoryBreakdownSectionLabel(scope: OutputPreviewRowScope): string {
  return scope === "flagged" ? "Flagged rows by category" : "Category breakdown";
}

const SEVERITY_PRIORITY: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
  none: 5,
};

function severitySortRank(value: string): number {
  const normalized = value.trim().toLowerCase();
  return SEVERITY_PRIORITY[normalized] ?? 6;
}

const OUTPUT_ROW_KEY_COLUMNS = [
  "expense_id",
  "transaction_id",
  "payout_id",
  "txn_id",
  "invoice_id",
  "id",
] as const;

function resolveColumnByNormalizedKey(
  columns: readonly string[],
  key: string,
): string | null {
  const normalized = normalizeColumnKey(key);
  for (const col of columns) {
    if (normalizeColumnKey(col) === normalized) return col;
  }
  return null;
}

/** Stable row key for review selection and export. */
export function resolveOutputRowKey(
  row: Record<string, string>,
  columns: readonly string[],
  rowIndex: number,
): string {
  for (const column of OUTPUT_ROW_KEY_COLUMNS) {
    const resolved = resolveColumnByNormalizedKey(columns, column);
    if (!resolved) continue;
    const value = (row[resolved] ?? "").trim();
    if (value) return value;
  }
  return `row-${rowIndex + 1}`;
}

/**
 * Flagged rows sorted by severity (critical first), then confidence when present.
 */
export function sortFlaggedRowsByPriority(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
): Array<Record<string, string>> {
  const confidenceColumn = resolveConfidenceColumn(columns);
  const indexed = rows.map((row, index) => ({ row, index }));
  return indexed
    .sort((a, b) => {
      const aSeverity = severitySortRank(
        deriveRowSeverity(a.row, columns, true),
      );
      const bSeverity = severitySortRank(
        deriveRowSeverity(b.row, columns, true),
      );
      if (aSeverity !== bSeverity) return aSeverity - bSeverity;
      if (confidenceColumn) {
        const aConf = parseRowConfidence(a.row, confidenceColumn);
        const bConf = parseRowConfidence(b.row, confidenceColumn);
        if (aConf !== bConf) return aConf - bConf;
      }
      return a.index - b.index;
    })
    .map(({ row }) => row);
}

export function filterRowsForReviewScope(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
  scope: OutputPreviewRowScope,
): Array<Record<string, string>> {
  if (scope === "all") return sortAllRowsForExpenseReview(rows, columns);
  const flagged = rows.filter((row) => isRowFlaggedForReview(row, columns));
  return sortFlaggedRowsByPriority(flagged, columns);
}

/** Flagged rows first (severity / confidence priority), then clean rows in file order. */
export function sortAllRowsForExpenseReview(
  rows: Array<Record<string, string>>,
  columns: readonly string[],
): Array<Record<string, string>> {
  const indexed = rows.map((row, index) => ({ row, index }));
  const flagged = indexed.filter(({ row }) => isRowFlaggedForReview(row, columns));
  const clean = indexed.filter(({ row }) => !isRowFlaggedForReview(row, columns));
  const sortedFlagged = sortFlaggedRowsByPriority(
    flagged.map(({ row }) => row),
    columns,
  );
  clean.sort((a, b) => a.index - b.index);
  return [...sortedFlagged, ...clean.map(({ row }) => row)];
}

export function supportsExpenseReviewQueue(columns: readonly string[]): boolean {
  return (
    resolveExceptionFlagColumn(columns) !== null ||
    resolveReviewRequiredColumn(columns) !== null
  );
}

const EXPENSE_REVIEW_SEARCH_FIELD_KEYS = [
  "expense_id",
  "employee_id",
  "category",
  "merchant",
] as const;

/**
 * Lowercase searchable text for a row in the expense review queue (visible fields).
 */
export function buildExpenseReviewRowSearchText(params: {
  readonly row: Record<string, string>;
  readonly columns: readonly string[];
  readonly displayPlan: ExpenseReviewDisplayPlan;
  readonly flagged: boolean;
  readonly severity: string;
  readonly reviewStatusLabel: string;
}): string {
  const parts: string[] = [params.reviewStatusLabel, params.severity];

  for (const key of EXPENSE_REVIEW_SEARCH_FIELD_KEYS) {
    const column = resolveColumnByNormalizedKey(params.columns, key);
    if (column) parts.push(params.row[column] ?? "");
  }

  for (const column of params.displayPlan.columns) {
    if (column.kind === "exception_reason") {
      parts.push(params.displayPlan.resolveCell(params.row, column));
    }
  }

  const severityColumn = resolveSeverityColumn(params.columns);
  if (severityColumn) {
    parts.push(params.row[severityColumn] ?? "");
  }

  return parts.join(" ").trim().toLowerCase();
}

export function expenseReviewRowMatchesSearch(
  query: string,
  rowSearchText: string,
): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return rowSearchText.includes(normalized);
}

function escapeCsvField(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

export type ExpenseReviewExportDecision = "pending" | "approve" | "reject";

export type ReviewedExpenseOutputFormat = "csv" | "xlsx" | "json" | "pdf";

export const REVIEWED_EXPENSE_OUTPUT_FILENAME_BASE = "reviewed_expense_output";

export function resolveExpenseReviewExportFields(
  flagged: boolean,
  decision: ExpenseReviewExportDecision,
): { review_status: string; review_decision: string } {
  if (!flagged) {
    return { review_status: "not_required", review_decision: "not_required" };
  }
  if (decision === "approve") {
    return { review_status: "reviewed", review_decision: "approved" };
  }
  if (decision === "reject") {
    return { review_status: "reviewed", review_decision: "rejected" };
  }
  return { review_status: "pending", review_decision: "pending" };
}

export function buildReviewedExpenseOutputColumns(
  parsed: ParsedCsv,
): string[] {
  return [...parsed.columns, "review_status", "review_decision"];
}

/** Row objects for reviewed export (all rows + review_status + review_decision). */
export function buildReviewedExpenseOutputRows(
  parsed: ParsedCsv,
  decisions: Readonly<Record<string, ExpenseReviewExportDecision>>,
): Array<Record<string, string>> {
  const exportColumns = buildReviewedExpenseOutputColumns(parsed);

  return parsed.rows.map((row, index) => {
    const rowKey = resolveOutputRowKey(row, parsed.columns, index);
    const flagged = isRowFlaggedForReview(row, parsed.columns);
    const decision = decisions[rowKey] ?? "pending";
    const { review_status, review_decision } = resolveExpenseReviewExportFields(
      flagged,
      decision,
    );

    const exported: Record<string, string> = {};
    for (const column of exportColumns) {
      if (column === "review_status") {
        exported[column] = review_status;
      } else if (column === "review_decision") {
        exported[column] = review_decision;
      } else {
        exported[column] = row[column] ?? "";
      }
    }
    return exported;
  });
}

/** Build reviewed export CSV from output rows and session triage decisions. */
export function buildReviewedExpenseOutputCsv(
  parsed: ParsedCsv,
  decisions: Readonly<Record<string, ExpenseReviewExportDecision>>,
): string {
  const exportColumns = buildReviewedExpenseOutputColumns(parsed);
  const rows = buildReviewedExpenseOutputRows(parsed, decisions);
  const lines: string[] = [exportColumns.map(escapeCsvField).join(",")];

  for (const row of rows) {
    lines.push(
      exportColumns.map((column) => escapeCsvField(row[column] ?? "")).join(","),
    );
  }

  return lines.join("\r\n");
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function downloadReviewedExpenseOutputCsv(
  parsed: ParsedCsv,
  decisions: Readonly<Record<string, ExpenseReviewExportDecision>>,
): void {
  downloadReviewedExpenseOutput(parsed, decisions, "csv");
}

export function downloadReviewedExpenseOutput(
  parsed: ParsedCsv,
  decisions: Readonly<Record<string, ExpenseReviewExportDecision>>,
  format: ReviewedExpenseOutputFormat,
): void {
  const rows = buildReviewedExpenseOutputRows(parsed, decisions);
  const columns = buildReviewedExpenseOutputColumns(parsed);

  switch (format) {
    case "csv": {
      const csv = buildReviewedExpenseOutputCsv(parsed, decisions);
      triggerBrowserDownload(
        new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }),
        `${REVIEWED_EXPENSE_OUTPUT_FILENAME_BASE}.csv`,
      );
      return;
    }
    case "xlsx": {
      const worksheet = XLSX.utils.json_to_sheet(rows, { header: columns });
      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, worksheet, "Reviewed Output");
      const buffer = XLSX.write(workbook, { bookType: "xlsx", type: "array" });
      triggerBrowserDownload(
        new Blob([buffer], {
          type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }),
        `${REVIEWED_EXPENSE_OUTPUT_FILENAME_BASE}.xlsx`,
      );
      return;
    }
    case "json": {
      triggerBrowserDownload(
        new Blob([JSON.stringify(rows, null, 2)], {
          type: "application/json;charset=utf-8",
        }),
        `${REVIEWED_EXPENSE_OUTPUT_FILENAME_BASE}.json`,
      );
      return;
    }
    case "pdf": {
      const doc = new jsPDF({
        orientation: columns.length > 6 ? "landscape" : "portrait",
        unit: "pt",
        format: "a4",
      });
      autoTable(doc, {
        head: [columns],
        body: rows.map((row) => columns.map((column) => row[column] ?? "")),
        styles: { fontSize: 8, cellPadding: 3, overflow: "linebreak" },
        headStyles: { fillColor: [15, 23, 42], textColor: 255 },
        margin: { top: 36, right: 24, bottom: 24, left: 24 },
      });
      doc.save(`${REVIEWED_EXPENSE_OUTPUT_FILENAME_BASE}.pdf`);
      return;
    }
    default: {
      const _exhaustive: never = format;
      return _exhaustive;
    }
  }
}

export function outputPreviewSupportsRowScopes(
  columns: readonly string[],
): boolean {
  return hasReviewSignalColumns(columns);
}

/** Horizontally resizable wrapper for overview preview blocks (breakdown + table). */
export function PreviewOverviewPanel({
  children,
  className,
}: {
  readonly children: ReactNode;
  readonly className?: string;
}) {
  return (
    <div
      className={cn(
        "min-w-72 w-full resize-x overflow-x-auto",
        className,
      )}
    >
      <div className="space-y-6">{children}</div>
    </div>
  );
}

export interface DataPreviewTableProps {
  readonly parsed: ParsedCsv;
  readonly maxRows?: number;
  readonly emptyMessage?: string;
  readonly totalRowHint?: number | null;
  readonly sectionLabel?: string;
  readonly ariaLabel?: string;
}

export function DataPreviewTable({
  parsed,
  maxRows = DEFAULT_PREVIEW_MAX_ROWS,
  emptyMessage = "No data rows to preview yet.",
  totalRowHint = null,
  sectionLabel = "Rows",
  ariaLabel = "Rows preview",
}: DataPreviewTableProps) {
  const displayRows = useMemo(
    () => sortRowsForReviewPreview(parsed.rows, parsed.columns),
    [parsed.rows, parsed.columns],
  );
  const head = displayRows.slice(0, maxRows);
  const displayPlan = useMemo(
    () => buildDisplayColumnPlan(parsed),
    [parsed],
  );
  const totalRows = totalRowHint ?? parsed.rows.length;

  if (head.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
        {emptyMessage}
      </p>
    );
  }

  return (
    <section aria-label={ariaLabel}>
      <PreviewSectionLabel>{sectionLabel}</PreviewSectionLabel>
      <div
        className="overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm"
        style={{ maxHeight: TABLE_MAX_HEIGHT }}
      >
        <table className="w-full min-w-max border-collapse text-sm">
          <thead className="sticky top-0 z-10 border-b border-slate-200 bg-white">
            <tr>
              {displayPlan.columns.map((col) => (
                <TH
                  key={col}
                  className={cn(
                    "whitespace-nowrap px-4 py-2.5 align-middle text-xs font-medium normal-case text-slate-500",
                    columnAlignClass(col),
                  )}
                >
                  {displayPlan.headerFor(col)}
                </TH>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {head.map((row, i) => (
              <tr key={i} className="transition-colors hover:bg-slate-50/80">
                {displayPlan.columns.map((col) => {
                  const value = row[col] ?? "";
                  return (
                    <PreviewCell key={col} column={col} value={value} />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalRows > maxRows ? (
        <p className="mt-2 text-xs text-slate-500">
          Download the full file to see all {totalRows.toLocaleString()} rows.
        </p>
      ) : null}
    </section>
  );
}

function PreviewCell({
  column,
  value,
}: {
  readonly column: string;
  readonly value: string;
}) {
  const align = columnAlignClass(column);
  const numeric = isAmountColumn(column);
  const trimmed = value.trim();
  let display = trimmed === "" ? "—" : value;
  let title: string | undefined;

  if (trimmed && isConfidenceColumn(column)) {
    const formatted = formatPreviewConfidence(trimmed);
    if (formatted) {
      display = formatted;
      if (value !== formatted) title = value;
    }
  } else if (trimmed && isHumanisedReviewColumn(column)) {
    const humanised = humaniseReviewFieldValue(column, value);
    display = humanised.display;
    if (humanised.technical) {
      title = humanised.technical;
    } else if (value.length > 32) {
      title = value;
    }
  } else if (value.length > 32) {
    title = value;
  }

  return (
    <TD
      className={cn(
        "max-w-[14rem] px-4 py-2.5 align-middle font-normal text-slate-800",
        align,
        (numeric || isConfidenceColumn(column)) && "tabular-nums",
        display === "—" && "text-slate-400",
      )}
      title={title}
    >
      <span className="block truncate">{display}</span>
    </TD>
  );
}
