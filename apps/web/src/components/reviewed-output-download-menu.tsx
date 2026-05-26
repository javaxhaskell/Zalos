"use client";

import { ChevronDown, Download } from "lucide-react";
import { useCallback } from "react";

import {
  downloadReviewedExpenseOutput,
  type ExpenseReviewExportDecision,
  type ParsedCsv,
  type ReviewedExpenseOutputFormat,
} from "@/components/data-preview-table";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { EXPENSE_REVIEW_QUEUE_COPY } from "@/lib/ux-language";

const OUTPUT_FORMATS: ReadonlyArray<{
  readonly format: ReviewedExpenseOutputFormat;
  readonly label: string;
}> = [
  { format: "csv", label: "CSV" },
  { format: "xlsx", label: "Excel" },
  { format: "json", label: "JSON" },
  { format: "pdf", label: "PDF" },
];

export interface ReviewedOutputDownloadMenuProps {
  readonly parsed: ParsedCsv;
  readonly decisions: Readonly<Record<string, ExpenseReviewExportDecision>>;
  readonly enabled: boolean;
}

export function ReviewedOutputDownloadMenu({
  parsed,
  decisions,
  enabled,
}: ReviewedOutputDownloadMenuProps) {
  const handleDownload = useCallback(
    (format: ReviewedExpenseOutputFormat) => {
      if (!enabled) return;
      downloadReviewedExpenseOutput(parsed, decisions, format);
    },
    [decisions, enabled, parsed],
  );

  if (!enabled) {
    return (
      <Button
        type="button"
        variant="primary"
        size="sm"
        disabled
        className="disabled:opacity-60"
      >
        <Download className="h-3.5 w-3.5" aria-hidden="true" />
        {EXPENSE_REVIEW_QUEUE_COPY.downloadReviewedOutputDisabled}
      </Button>
    );
  }

  return (
    <DropdownMenu
      align="start"
      side="bottom"
      menuClassName="mt-1 max-h-24 overflow-y-auto"
      trigger={
        <Button type="button" variant="primary" size="sm" aria-haspopup="menu">
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
          {EXPENSE_REVIEW_QUEUE_COPY.downloadReviewedOutput}
          <ChevronDown className="h-3.5 w-3.5 opacity-80" aria-hidden="true" />
        </Button>
      }
    >
      {OUTPUT_FORMATS.map(({ format, label }) => (
        <DropdownMenuItem key={format} onSelect={() => handleDownload(format)}>
          {label}
        </DropdownMenuItem>
      ))}
    </DropdownMenu>
  );
}
