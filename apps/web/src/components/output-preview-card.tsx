"use client";

import { useEffect, useMemo, useState } from "react";

import {
  breakdownSectionLabelForColumn,
  categoryBreakdownSectionLabel,
  DataPreviewTable,
  DEFAULT_PREVIEW_MAX_ROWS,
  detectBreakdownDimensionColumn,
  outputPreviewTableSectionLabel,
  PreviewOverviewPanel,
  parseCsv,
  previewViewTitle,
  supportsExpenseReviewQueue,
  type ParsedCsv,
  type PreviewViewMode,
} from "@/components/data-preview-table";
import { CategoryBreakdown } from "@/components/category-breakdown";
import { ExpenseReviewQueue } from "@/components/expense-review-queue";
import { SkeletonCard, SkeletonTableRows } from "@/components/motion";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { fetchArtifactText, type WorkspaceEvent } from "@/lib/api-client";
import { deriveAuthorWorkflowContext } from "@/lib/ux-language";

/**
 * Preview of ``outputs/output.csv`` — category distribution and row table with
 * optional flagged-vs-all scope when the output carries review signals.
 */
export interface OutputPreviewCardProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

export function OutputPreviewCard({
  sessionId,
  events,
}: OutputPreviewCardProps) {
  const outputPath = findOutputPath(events);
  const workflowContext = useMemo(
    () => deriveAuthorWorkflowContext(events),
    [events],
  );
  const isPaymentReconciliation = workflowContext.isPaymentProcessorReconciliation;
  const [parsed, setParsed] = useState<ParsedCsv | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const viewMode: PreviewViewMode = "overview";
  const supportsReviewQueue = parsed
    ? supportsExpenseReviewQueue(parsed.columns)
    : false;
  const breakdownDimensionColumn = parsed
    ? detectBreakdownDimensionColumn(parsed.columns)
    : null;
  const breakdownLabel = breakdownDimensionColumn
    ? breakdownSectionLabelForColumn(breakdownDimensionColumn)
    : categoryBreakdownSectionLabel("all");

  useEffect(() => {
    if (!outputPath) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchArtifactText(sessionId, outputPath)
      .then((text) => {
        if (cancelled) return;
        try {
          setParsed(parseCsv(text));
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, outputPath]);

  if (!outputPath) return null;

  return (
    <Card className="border-slate-200/90 shadow-sm">
      <CardHeader className="border-b border-slate-100 bg-white px-6 pb-4 pt-6">
        <h3 className="text-lg font-semibold leading-none tracking-tight text-slate-900">
          {previewViewTitle(viewMode)}
        </h3>
      </CardHeader>
      <CardContent className="space-y-6 px-6 pb-6 pt-6">
        {loading ? (
          <div className="space-y-4" aria-busy="true" aria-label="Loading output preview">
            <SkeletonTableRows rows={6} cols={4} />
            <SkeletonCard lines={2} className="border-0 shadow-none" />
          </div>
        ) : null}
        {error ? (
          <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            We could not load a preview. Use the download link above to
            open the full file.
          </p>
        ) : null}
        {parsed ? (
          <PreviewOverviewPanel>
            <CategoryBreakdown
              rows={parsed.rows}
              columns={parsed.columns}
              isPaymentReconciliation={isPaymentReconciliation}
              categoryColumn={breakdownDimensionColumn ?? undefined}
              sectionLabel={breakdownLabel}
            />
            {supportsReviewQueue ? (
              <ExpenseReviewQueue
                sessionId={sessionId}
                parsed={parsed}
                totalRowCount={parsed.rows.length}
                sectionLabel={outputPreviewTableSectionLabel("all")}
                ariaLabel="All output rows"
              />
            ) : (
              <DataPreviewTable
                parsed={parsed}
                maxRows={
                  viewMode === "overview"
                    ? parsed.rows.length
                    : DEFAULT_PREVIEW_MAX_ROWS
                }
                totalRowHint={parsed.rows.length}
                sectionLabel={outputPreviewTableSectionLabel("all")}
                ariaLabel="All output rows"
              />
            )}
          </PreviewOverviewPanel>
        ) : null}
      </CardContent>
    </Card>
  );
}

function findOutputPath(
  events: ReadonlyArray<WorkspaceEvent>,
): string | null {
  let outputPath: string | null = null;
  for (const ev of events) {
    if (ev.kind === "artifact_generated") {
      const p = ev.payload as Record<string, unknown>;
      const artifactType = p.artifact_type;
      const path = typeof p.path === "string" ? p.path : null;
      if (!path) continue;
      if (
        artifactType === "workflow_row_output" ||
        (artifactType === "output_csv" &&
          path.endsWith(".csv") &&
          path.startsWith("outputs/"))
      ) {
        outputPath = path;
      }
    }
  }
  if (outputPath) return outputPath;
  for (const ev of events) {
    if (ev.kind === "decision_input") {
      const p = ev.payload as Record<string, unknown>;
      if (p.kind === "template_execution" && typeof p.output_path === "string") {
        return p.output_path;
      }
    }
  }
  return null;
}
