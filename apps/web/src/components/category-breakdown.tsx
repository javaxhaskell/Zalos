"use client";

import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";

import {
  PreviewSectionLabel,
  deriveRowSeverity,
  detectBreakdownDimensionColumn,
  formatPreviewCurrency,
  isRowFlaggedForReview,
  parseNumericAmount,
  resolveAmountBreakdownContext,
} from "@/components/data-preview-table";
import { Badge } from "@/components/ui/badge";
import { resolveOutputRowDisplayId } from "@/lib/ux-language";
import { cn } from "@/lib/utils";

export type CategoryBreakdownMetric = "count" | "amount";

export interface CategoryBreakdownProps {
  readonly rows: Array<Record<string, string>>;
  readonly columns?: readonly string[];
  readonly isPaymentReconciliation?: boolean;
  readonly categoryColumn?: string;
  readonly sectionLabel?: string;
}

interface CountEntry {
  readonly category: string;
  readonly value: number;
  readonly share: number;
}

interface AmountEntry {
  readonly category: string;
  readonly value: number;
  readonly share: number;
}

interface CategoryRowItem {
  readonly row: Record<string, string>;
  readonly index: number;
  readonly amount: number | null;
}

export function CategoryBreakdown({
  rows,
  columns,
  isPaymentReconciliation = false,
  categoryColumn,
  sectionLabel = "Category breakdown",
}: CategoryBreakdownProps) {
  const [metric, setMetric] = useState<CategoryBreakdownMetric>("count");
  const [expandedCategories, setExpandedCategories] = useState<
    ReadonlySet<string>
  >(() => new Set());

  const first = rows[0];
  const resolvedColumns = useMemo(
    () => columns ?? (first ? Object.keys(first) : []),
    [columns, first],
  );

  const resolvedCategoryColumn = useMemo(
    () =>
      categoryColumn && resolvedColumns.includes(categoryColumn)
        ? categoryColumn
        : detectBreakdownDimensionColumn(resolvedColumns),
    [categoryColumn, resolvedColumns],
  );

  const amountContext = useMemo(
    () => resolveAmountBreakdownContext(resolvedColumns, rows),
    [resolvedColumns, rows],
  );

  const countEntries = useMemo(
    () =>
      resolvedCategoryColumn
        ? computeCountBreakdown(rows, resolvedCategoryColumn)
        : [],
    [rows, resolvedCategoryColumn],
  );

  const amountEntries = useMemo(
    () =>
      resolvedCategoryColumn &&
      amountContext &&
      !amountContext.mixedCurrency
        ? computeAmountBreakdown(
            rows,
            resolvedCategoryColumn,
            amountContext.amountColumn,
          )
        : [],
    [rows, resolvedCategoryColumn, amountContext],
  );

  const rowsByCategory = useMemo(
    () =>
      resolvedCategoryColumn
        ? groupRowsByCategory(
            rows,
            resolvedCategoryColumn,
            amountContext?.amountColumn ?? null,
          )
        : new Map<string, CategoryRowItem[]>(),
    [rows, resolvedCategoryColumn, amountContext?.amountColumn],
  );

  if (
    !first ||
    !resolvedCategoryColumn ||
    !(resolvedCategoryColumn in first) ||
    isPaymentReconciliation
  ) {
    return null;
  }

  const canShowAmount =
    amountContext !== null && !amountContext.mixedCurrency && amountEntries.length > 0;
  const activeMetric =
    metric === "amount" && canShowAmount ? "amount" : "count";
  const entries = activeMetric === "amount" ? amountEntries : countEntries;
  const maxBarMagnitude =
    activeMetric === "amount"
      ? Math.max(...entries.map((entry) => Math.abs(entry.value)), 1)
      : (entries[0]?.value ?? 1);

  const toggleCategory = (category: string) => {
    setExpandedCategories((current) => {
      const next = new Set(current);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  };

  return (
    <section aria-label="Category breakdown">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <PreviewSectionLabel className="mb-0">
          {sectionLabel}
        </PreviewSectionLabel>
        {canShowAmount ? (
          <MetricToggle metric={activeMetric} onMetricChange={setMetric} />
        ) : null}
      </div>
      {amountContext?.mixedCurrency ? (
        <p className="mb-3 text-sm text-slate-600">
          This file uses more than one currency, so only count breakdown is
          shown.
        </p>
      ) : null}
      <div className="space-y-3.5 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {entries.map(({ category, value, share }) => {
          const magnitude =
            activeMetric === "amount" ? Math.abs(value) : value;
          const barWidth = Math.max(4, (magnitude / maxBarMagnitude) * 100);
          const displayValue =
            activeMetric === "amount"
              ? formatPreviewCurrency(value, amountContext?.currency ?? null)
              : value.toLocaleString();
          const amountStyles =
            activeMetric === "amount"
              ? getAmountSignStyles(value)
              : null;
          const barColorClass =
            amountStyles?.barClass ?? "bg-slate-400/90";
          const isExpanded = expandedCategories.has(category);
          const categoryRows = rowsByCategory.get(category) ?? [];
          const lineItems =
            activeMetric === "amount"
              ? categoryRows.filter((item) => item.amount !== null)
              : categoryRows;

          return (
            <div key={category} className="space-y-1.5">
              <button
                type="button"
                onClick={() => toggleCategory(category)}
                aria-expanded={isExpanded}
                className="flex min-h-8 w-full items-center justify-between gap-3 rounded-md px-0.5 text-left text-sm transition-colors hover:bg-slate-50/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400/80 focus-visible:ring-offset-1"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <ChevronDown
                    aria-hidden
                    className={cn(
                      "h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform duration-200",
                      isExpanded && "rotate-180",
                    )}
                  />
                  <span className="font-medium text-slate-900">{category}</span>
                </span>
                <span
                  className={cn(
                    "shrink-0 tabular-nums leading-none",
                    amountStyles?.amountClass ?? "text-slate-600",
                  )}
                >
                  {displayValue}
                  <span className="ml-1 text-slate-400">
                    ({share.toFixed(0)}%)
                  </span>
                </span>
              </button>
              <div
                className="h-1.5 overflow-hidden rounded-full bg-slate-100"
                role="presentation"
              >
                <div
                  className={cn(
                    "h-full rounded-full transition-[width]",
                    barColorClass,
                  )}
                  style={{ width: `${barWidth}%` }}
                />
              </div>
              {isExpanded && lineItems.length > 0 ? (
                <ul className="ml-5 space-y-1 border-l border-slate-100 py-0.5 pl-3">
                  {lineItems.map(({ row, index, amount }) => {
                    const label = resolveBreakdownRowLabel(row, index);
                    const flagged = isRowFlaggedForReview(row, resolvedColumns);
                    const severity = deriveRowSeverity(row, resolvedColumns, flagged);
                    const itemAmountStyles =
                      amount !== null ? getAmountSignStyles(amount) : null;
                    return (
                      <li
                        key={`${category}-${index}`}
                        className="flex min-h-6 items-center justify-between gap-3 text-xs"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span
                            className="min-w-0 truncate text-slate-600"
                            title={label}
                          >
                            {label}
                          </span>
                          {flagged && severity ? (
                            <Badge
                              variant={severityBadgeVariant(severity)}
                              className="h-4 shrink-0 px-1.5 text-[10px] leading-none"
                            >
                              {formatSeverityLabel(severity)}
                            </Badge>
                          ) : null}
                        </span>
                        {activeMetric === "amount" && amount !== null ? (
                          <span
                            className={cn(
                              "shrink-0 tabular-nums",
                              itemAmountStyles?.amountClass ?? "text-slate-500",
                            )}
                          >
                            {formatPreviewCurrency(
                              amount,
                              amountContext?.currency ?? null,
                            )}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function resolveBreakdownRowLabel(
  row: Record<string, string>,
  index: number,
): string {
  const description = (row.description ?? "").trim();
  if (description) return description;

  const counterparty = (row.counterparty ?? "").trim();
  if (counterparty) return counterparty;

  const customer = (row.customer ?? "").trim();
  if (customer) return customer;

  const vendor = (row.vendor ?? row.vendor_name ?? "").trim();
  if (vendor) return vendor;

  return resolveOutputRowDisplayId(row, index);
}

function groupRowsByCategory(
  rows: Array<Record<string, string>>,
  categoryColumn: string,
  amountColumn: string | null,
): Map<string, CategoryRowItem[]> {
  const groups = new Map<string, CategoryRowItem[]>();
  rows.forEach((row, index) => {
    const category = (row[categoryColumn] ?? "").trim() || "Uncategorised";
    const amount = amountColumn
      ? parseNumericAmount(row[amountColumn] ?? "")
      : null;
    const existing = groups.get(category) ?? [];
    existing.push({ row, index, amount });
    groups.set(category, existing);
  });
  return groups;
}

function getAmountSignStyles(value: number): {
  readonly barClass: string;
  readonly amountClass: string;
} {
  if (value > 0) {
    return {
      barClass: "bg-emerald-500/80",
      amountClass: "text-emerald-700",
    };
  }
  if (value < 0) {
    return {
      barClass: "bg-rose-400/90",
      amountClass: "text-rose-600",
    };
  }
  return {
    barClass: "bg-slate-400/90",
    amountClass: "text-slate-500",
  };
}

function MetricToggle({
  metric,
  onMetricChange,
}: {
  readonly metric: CategoryBreakdownMetric;
  readonly onMetricChange: (metric: CategoryBreakdownMetric) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Category breakdown metric"
      className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5"
    >
      <MetricToggleButton
        label="By count"
        isActive={metric === "count"}
        onClick={() => onMetricChange("count")}
      />
      <MetricToggleButton
        label="By amount"
        isActive={metric === "amount"}
        onClick={() => onMetricChange("amount")}
      />
    </div>
  );
}

function MetricToggleButton({
  label,
  isActive,
  onClick,
}: {
  readonly label: string;
  readonly isActive: boolean;
  readonly onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      onClick={onClick}
      className={cn(
        "rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400/80 focus-visible:ring-offset-1",
        isActive
          ? "bg-white text-slate-900 shadow-sm"
          : "text-slate-500 hover:text-slate-800",
      )}
    >
      {label}
    </button>
  );
}

function computeCountBreakdown(
  rows: Array<Record<string, string>>,
  categoryColumn: string,
): CountEntry[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const category = (row[categoryColumn] ?? "").trim() || "Uncategorised";
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }
  const total = rows.length || 1;
  return [...counts.entries()]
    .map(([category, value]) => ({
      category,
      value,
      share: (value / total) * 100,
    }))
    .sort((a, b) => b.value - a.value);
}

function computeAmountBreakdown(
  rows: Array<Record<string, string>>,
  categoryColumn: string,
  amountColumn: string,
): AmountEntry[] {
  const totals = new Map<string, number>();
  for (const row of rows) {
    const category = (row[categoryColumn] ?? "").trim() || "Uncategorised";
    const parsed = parseNumericAmount(row[amountColumn] ?? "");
    if (parsed === null) continue;
    totals.set(category, (totals.get(category) ?? 0) + parsed);
  }
  const totalMagnitude =
    [...totals.values()].reduce((sum, value) => sum + Math.abs(value), 0) || 1;
  return [...totals.entries()]
    .map(([category, value]) => ({
      category,
      value,
      share: (Math.abs(value) / totalMagnitude) * 100,
    }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
}

function formatSeverityLabel(severity: string): string {
  const trimmed = severity.trim();
  if (!trimmed || trimmed.toLowerCase() === "none") return "None";
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
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
