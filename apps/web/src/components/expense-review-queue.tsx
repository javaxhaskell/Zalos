"use client";

import { AlertCircle, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  buildExpenseReviewDisplayColumnPlan,
  buildExpenseReviewRowSearchText,
  deriveRowSeverity,
  expenseReviewColumnWidthClass,
  expenseReviewRowMatchesSearch,
  formatPreviewConfidence,
  isConfidenceColumn,
  isRowFlaggedForReview,
  PreviewSectionLabel,
  resolveOutputRowKey,
  sortAllRowsForExpenseReview,
  TABLE_MAX_HEIGHT,
  type ExpenseReviewColumn,
  type ExpenseReviewExportDecision,
  type ParsedCsv,
} from "@/components/data-preview-table";
import { ReviewedOutputDownloadMenu } from "@/components/reviewed-output-download-menu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TD, TH } from "@/components/ui/table";
import {
  EXPENSE_REVIEW_QUEUE_COPY,
  humaniseReviewFieldValue,
  isHumanisedReviewColumn,
  REVIEW_TABLE_COLUMNS,
} from "@/lib/ux-language";
import { cn } from "@/lib/utils";

export type ReviewDecision = ExpenseReviewExportDecision;

interface PersistedReviewState {
  selectedIds: string[];
  decisions: Record<string, ReviewDecision>;
}

export interface ExpenseReviewQueueProps {
  readonly sessionId: string;
  readonly parsed: ParsedCsv;
  readonly totalRowCount: number;
  readonly sectionLabel: string;
  readonly ariaLabel: string;
}

const VALID_DECISIONS = new Set<ReviewDecision>(["pending", "approve", "reject"]);

/** Fixed width keeps checkbox, icon, and badges on one baseline across rows. */
const REVIEW_COLUMN_CLASS = "w-[13rem] min-w-[13rem] max-w-[13rem] align-middle px-3 py-2";

const REVIEW_BADGE_CLASS = "h-5 shrink-0 px-2 text-[11px] leading-none";

function reviewStorageKey(sessionId: string): string {
  return `agentforge:expense-review:${sessionId}`;
}

function normalizeDecision(value: unknown): ReviewDecision {
  if (typeof value !== "string") return "pending";
  if (value === "assigned" || value === "needs_evidence") return "pending";
  if (VALID_DECISIONS.has(value as ReviewDecision)) {
    return value as ReviewDecision;
  }
  return "pending";
}

function loadPersistedState(sessionId: string): PersistedReviewState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(reviewStorageKey(sessionId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as {
      selectedIds?: unknown;
      decisions?: unknown;
    };
    if (!parsed || typeof parsed !== "object") return null;

    const decisions: Record<string, ReviewDecision> = {};
    if (parsed.decisions && typeof parsed.decisions === "object") {
      for (const [rowKey, decision] of Object.entries(parsed.decisions)) {
        decisions[rowKey] = normalizeDecision(decision);
      }
    }

    return {
      selectedIds: Array.isArray(parsed.selectedIds)
        ? parsed.selectedIds.filter((id): id is string => typeof id === "string")
        : [],
      decisions,
    };
  } catch {
    return null;
  }
}

function persistState(sessionId: string, state: PersistedReviewState): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(reviewStorageKey(sessionId), JSON.stringify(state));
  } catch {
    // Ignore quota / private-mode errors — UI state stays in memory.
  }
}

function severityBadgeVariant(
  severity: string,
): "neutral" | "info" | "success" | "warning" | "danger" {
  switch (severity.trim().toLowerCase()) {
    case "critical":
    case "high":
      return "danger";
    case "medium":
      return "warning";
    case "low":
      return "success";
    case "info":
      return "info";
    default:
      return "neutral";
  }
}

function formatSeverityLabel(severity: string): string {
  const trimmed = severity.trim();
  if (!trimmed || trimmed.toLowerCase() === "none") return "None";
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
}

function isAmountColumn(column: string): boolean {
  return /^(amount|total|value|price|debit|credit|balance|fee|net|gross|subtotal|tax|payment|paid|due|payment_amount)$/i.test(
    column,
  );
}

function columnAlignClass(column: string): string {
  return isAmountColumn(column) ? "text-right" : "text-left";
}

function rowBackgroundClass(flagged: boolean, decision: ReviewDecision): string {
  if (!flagged) return "";
  switch (decision) {
    case "approve":
      return "bg-emerald-50/40";
    case "reject":
      return "bg-red-50/30";
    default:
      return "bg-amber-50/20";
  }
}

function reviewStatusSearchLabel(
  flagged: boolean,
  decision: ReviewDecision,
): string {
  if (!flagged) return EXPENSE_REVIEW_QUEUE_COPY.statusNoIssue;
  if (decision === "approve") return EXPENSE_REVIEW_QUEUE_COPY.decisionApproved;
  if (decision === "reject") return EXPENSE_REVIEW_QUEUE_COPY.decisionRejected;
  return EXPENSE_REVIEW_QUEUE_COPY.statusReviewNeeded;
}

export function ExpenseReviewQueue({
  sessionId,
  parsed,
  totalRowCount,
  sectionLabel,
  ariaLabel,
}: ExpenseReviewQueueProps) {
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [decisions, setDecisions] = useState<Readonly<Record<string, ReviewDecision>>>({});
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    const stored = loadPersistedState(sessionId);
    if (!stored) return;
    setSelectedIds(new Set(stored.selectedIds));
    setDecisions(stored.decisions);
  }, [sessionId]);

  useEffect(() => {
    persistState(sessionId, {
      selectedIds: [...selectedIds],
      decisions: { ...decisions },
    });
  }, [sessionId, selectedIds, decisions]);

  const flaggedRows = useMemo(
    () =>
      parsed.rows
        .map((row, index) => ({
          row,
          rowKey: resolveOutputRowKey(row, parsed.columns, index),
          flagged: isRowFlaggedForReview(row, parsed.columns),
        }))
        .filter(({ flagged }) => flagged),
    [parsed.rows, parsed.columns],
  );

  const displayRows = useMemo(() => {
    const sortedRows = sortAllRowsForExpenseReview(parsed.rows, parsed.columns);
    return sortedRows.map((row) => {
      const index = parsed.rows.indexOf(row);
      const resolvedIndex = index >= 0 ? index : 0;
      return {
        row,
        rowKey: resolveOutputRowKey(row, parsed.columns, resolvedIndex),
        flagged: isRowFlaggedForReview(row, parsed.columns),
      };
    });
  }, [parsed.columns, parsed.rows]);

  const displayPlan = useMemo(
    () => buildExpenseReviewDisplayColumnPlan(parsed),
    [parsed],
  );

  const filteredDisplayRows = useMemo(() => {
    return displayRows.filter(({ row, rowKey, flagged }) => {
      const decision = decisions[rowKey] ?? "pending";
      const severity = deriveRowSeverity(row, parsed.columns, flagged);
      const searchText = buildExpenseReviewRowSearchText({
        row,
        columns: parsed.columns,
        displayPlan,
        flagged,
        severity,
        reviewStatusLabel: reviewStatusSearchLabel(flagged, decision),
      });
      return expenseReviewRowMatchesSearch(searchQuery, searchText);
    });
  }, [decisions, displayPlan, displayRows, parsed.columns, searchQuery]);

  const allFlaggedReviewed = useMemo(() => {
    if (flaggedRows.length === 0) return true;
    return flaggedRows.every(({ rowKey }) => {
      const decision = decisions[rowKey] ?? "pending";
      return decision === "approve" || decision === "reject";
    });
  }, [decisions, flaggedRows]);

  const toggleRowSelected = useCallback((rowKey: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) next.add(rowKey);
      else next.delete(rowKey);
      return next;
    });
  }, []);

  const selectAllFlagged = useCallback(() => {
    setSelectedIds(new Set(flaggedRows.map(({ rowKey }) => rowKey)));
  }, [flaggedRows]);

  const unselectAllFlagged = useCallback(() => {
    const flaggedKeys = new Set(flaggedRows.map(({ rowKey }) => rowKey));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const rowKey of flaggedKeys) {
        next.delete(rowKey);
      }
      return next;
    });
  }, [flaggedRows]);

  const applyBulkDecision = useCallback(
    (decision: ReviewDecision) => {
      const selectedFlagged = flaggedRows.filter(({ rowKey }) => selectedIds.has(rowKey));
      if (selectedFlagged.length === 0) return;

      setDecisions((prev) => {
        const next = { ...prev };
        for (const { rowKey } of selectedFlagged) {
          next[rowKey] = decision;
        }
        return next;
      });
    },
    [flaggedRows, selectedIds],
  );

  const selectedFlaggedCount = flaggedRows.filter(({ rowKey }) =>
    selectedIds.has(rowKey),
  ).length;

  if (displayRows.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
        No data rows to preview yet.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <section aria-label={ariaLabel}>
        <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
          <PreviewSectionLabel className="mb-0">{sectionLabel}</PreviewSectionLabel>
          {flaggedRows.length > 0 ? (
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-slate-600"
                onClick={selectAllFlagged}
              >
                Select all
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-slate-600"
                onClick={unselectAllFlagged}
              >
                Unselect all
              </Button>
            </div>
          ) : null}
        </div>
        <div className="relative mb-4 max-w-md">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          />
          <input
            type="search"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder={EXPENSE_REVIEW_QUEUE_COPY.searchPlaceholder}
            aria-label={EXPENSE_REVIEW_QUEUE_COPY.searchAriaLabel}
            className="w-full rounded-md border border-slate-300 py-1.5 pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
          />
        </div>
        <div
          className="overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm"
          style={{ maxHeight: TABLE_MAX_HEIGHT }}
        >
          <table className="w-full min-w-max table-fixed border-collapse text-sm">
            <colgroup>
              <col className="w-[13rem]" />
              {displayPlan.columns.map((col) => (
                <col key={col.key} className={expenseReviewColumnWidthClass(col)} />
              ))}
            </colgroup>
            <thead className="sticky top-0 z-10 border-b border-slate-200 bg-white">
              <tr>
                <TH
                  className={cn(
                    REVIEW_COLUMN_CLASS,
                    "whitespace-nowrap text-left text-xs font-medium normal-case text-slate-500",
                  )}
                >
                  {REVIEW_TABLE_COLUMNS.review.label}
                </TH>
                {displayPlan.columns.map((col) => (
                  <TH
                    key={col.key}
                    className={cn(
                      expenseReviewColumnWidthClass(col),
                      "whitespace-nowrap px-3 py-2 align-middle text-xs font-medium normal-case text-slate-500",
                      columnAlignClass(displayPlan.humaniseAs(col)),
                    )}
                  >
                    {displayPlan.headerFor(col)}
                  </TH>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredDisplayRows.length === 0 ? (
                <tr>
                  <TD
                    colSpan={displayPlan.columns.length + 1}
                    className="px-4 py-8 text-center text-sm text-slate-500"
                  >
                    No rows match your search.
                  </TD>
                </tr>
              ) : (
                filteredDisplayRows.map(({ row, rowKey, flagged }) => {
                  const decision = decisions[rowKey] ?? "pending";
                  const severity = deriveRowSeverity(row, parsed.columns, flagged);
                  return (
                    <tr
                      key={rowKey}
                      className={cn(
                        "transition-colors hover:bg-slate-50/80",
                        rowBackgroundClass(flagged, decision),
                      )}
                    >
                      <TD className={REVIEW_COLUMN_CLASS}>
                        <ReviewStartCell
                          rowKey={rowKey}
                          flagged={flagged}
                          decision={decision}
                          severity={severity}
                          selected={selectedIds.has(rowKey)}
                          onToggleSelected={toggleRowSelected}
                        />
                      </TD>
                      {displayPlan.columns.map((col) => (
                        <ReviewDataCell
                          key={col.key}
                          reviewColumn={col}
                          column={displayPlan.humaniseAs(col)}
                          value={displayPlan.resolveCell(row, col)}
                        />
                      ))}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {totalRowCount > parsed.rows.length ? (
          <p className="mt-2 text-xs text-slate-500">
            Download the full file to see all {totalRowCount.toLocaleString()} rows.
          </p>
        ) : null}
      </section>

      <section
        aria-label={EXPENSE_REVIEW_QUEUE_COPY.panelTitle}
        className="mt-8 pt-2"
      >
        <div className="mb-4 flex min-h-8 flex-wrap items-center justify-between gap-3">
          <h4 className="text-sm font-semibold leading-snug text-slate-900">
            {EXPENSE_REVIEW_QUEUE_COPY.panelTitle}
          </h4>
          {selectedFlaggedCount > 0 ? (
            <span className="text-xs leading-snug text-slate-600">
              {EXPENSE_REVIEW_QUEUE_COPY.selectedFlaggedCount(selectedFlaggedCount)}
            </span>
          ) : null}
        </div>
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant="success"
              size="sm"
              onClick={() => applyBulkDecision("approve")}
              disabled={selectedFlaggedCount === 0}
            >
              {EXPENSE_REVIEW_QUEUE_COPY.bulkApprove}
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => applyBulkDecision("reject")}
              disabled={selectedFlaggedCount === 0}
            >
              {EXPENSE_REVIEW_QUEUE_COPY.bulkReject}
            </Button>
            <ReviewedOutputDownloadMenu
              parsed={parsed}
              decisions={decisions}
              enabled={allFlaggedReviewed}
            />
          </div>
          <div className="relative h-24 shrink-0" aria-hidden="true" />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-slate-600">
          {EXPENSE_REVIEW_QUEUE_COPY.validationNote}
        </p>
      </section>
    </div>
  );
}

function ReviewStartCell({
  rowKey,
  flagged,
  decision,
  severity,
  selected,
  onToggleSelected,
}: {
  readonly rowKey: string;
  readonly flagged: boolean;
  readonly decision: ReviewDecision;
  readonly severity: string;
  readonly selected: boolean;
  readonly onToggleSelected: (rowKey: string, selected: boolean) => void;
}) {
  return (
    <div className="flex min-h-5 items-center gap-2.5">
      {flagged ? (
        <input
          type="checkbox"
          className="h-4 w-4 shrink-0 rounded border-slate-300 text-slate-900 focus:ring-slate-500"
          checked={selected}
          onChange={(event) => onToggleSelected(rowKey, event.target.checked)}
          aria-label={`Select ${rowKey}`}
        />
      ) : (
        <input
          type="checkbox"
          disabled
          checked={false}
          className="h-4 w-4 shrink-0 cursor-not-allowed rounded border-slate-200 bg-slate-100 text-slate-300 opacity-70"
          aria-label={EXPENSE_REVIEW_QUEUE_COPY.statusNoIssue}
        />
      )}
      {flagged ? (
        <div className="min-w-0 flex-1">
          <ReviewStatusCell decision={decision} severity={severity} />
        </div>
      ) : null}
    </div>
  );
}

function ReviewStatusCell({
  decision,
  severity,
}: {
  readonly decision: ReviewDecision;
  readonly severity: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {decision === "pending" ? (
        <span
          className="inline-flex h-4 w-4 shrink-0 items-center justify-center text-amber-600"
          aria-label="Review needed"
        >
          <AlertCircle className="h-4 w-4" aria-hidden />
        </span>
      ) : (
        <DecisionBadge decision={decision} />
      )}
      <SeverityBadge severity={severity} />
    </div>
  );
}

function DecisionBadge({ decision }: { readonly decision: ReviewDecision }) {
  switch (decision) {
    case "approve":
      return (
        <Badge variant="success" className={REVIEW_BADGE_CLASS}>
          {EXPENSE_REVIEW_QUEUE_COPY.decisionApproved}
        </Badge>
      );
    case "reject":
      return (
        <Badge variant="danger" className={REVIEW_BADGE_CLASS}>
          {EXPENSE_REVIEW_QUEUE_COPY.decisionRejected}
        </Badge>
      );
    default:
      return null;
  }
}

function SeverityBadge({ severity }: { readonly severity: string }) {
  const normalized = severity.trim().toLowerCase();
  if (!normalized || normalized === "none") return null;
  return (
    <Badge variant={severityBadgeVariant(normalized)} className={REVIEW_BADGE_CLASS}>
      {formatSeverityLabel(severity)}
    </Badge>
  );
}

function ReviewDataCell({
  reviewColumn,
  column,
  value,
}: {
  readonly reviewColumn: ExpenseReviewColumn;
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

  const cellTitle = title ?? (trimmed ? value : undefined);

  return (
    <TD
      className={cn(
        expenseReviewColumnWidthClass(reviewColumn),
        "whitespace-nowrap px-3 py-2 align-middle font-normal text-slate-800",
        align,
        (numeric || isConfidenceColumn(column)) && "tabular-nums",
        display === "—" && "text-slate-400",
      )}
      title={cellTitle}
    >
      <span className="block truncate">{display}</span>
    </TD>
  );
}
