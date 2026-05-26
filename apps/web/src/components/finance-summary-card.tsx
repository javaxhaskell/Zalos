"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SmoothCollapse } from "@/components/motion/smooth-collapse";
import {
  countRowsFlaggedForReview,
  parseCsv,
} from "@/components/data-preview-table";
import {
  archiveUrl,
  artifactUrl,
  fetchArtifactText,
  type WorkspaceEvent,
} from "@/lib/api-client";
import {
  AUTHOR_SUCCESS_COPY,
  authorSuccessBadgeLabel,
  authorSuccessCardTitle,
  authorSuccessQualityLabel,
  friendlyBasename,
  humaniseArtifactPathLabel,
  humaniseWorkflowLabel,
  type AuthorSuccessValidationStatus,
} from "@/lib/ux-language";
import { extractAuthorWorkflowRequestText } from "@/lib/workflow-title";
import { cn } from "@/lib/utils";

/**
 * The first thing a finance user sees on a completed Author session.
 * Plain-English summary and download actions only; technical metadata
 * lives in the collapsed section below.
 */
export interface FinanceSummaryCardProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

interface FinanceSummary {
  workflowLabel: string;
  templateName: string | null;
  workflowType: string | null;
  inputFilename: string | null;
  inputFormat: string | null;
  selectedSheet: string | null;
  inputSizeBytes: number | null;
  rowsProcessed: number | null;
  outputPath: string | null;
  validationStatus: AuthorSuccessValidationStatus;
  validationCounts: { passed: number; failed: number; skipped: number };
  skippedLayers: string[];
  warnings: string[];
  pytestPassed: number | null;
  pytestTotal: number | null;
  pytestRan: boolean;
  completionVia: string | null;
  modelCallCount: number;
  isCustomWorkflowBuild: boolean;
  isPaymentProcessorReconciliation: boolean;
  summaryOutputFiles: string[];
  workflowReportPath: string | null;
}

export function FinanceSummaryCard({
  sessionId,
  events,
}: FinanceSummaryCardProps) {
  const summary = useMemo(() => deriveSummary(events), [events]);
  const outputPath = summary.outputPath;
  const [outputStats, setOutputStats] = useState<{
    rows: number | null;
    flaggedForReview: number | null;
  }>({ rows: null, flaggedForReview: null });

  useEffect(() => {
    if (!outputPath) return;
    let cancelled = false;
    void fetchArtifactText(sessionId, outputPath)
      .then((text) => {
        if (cancelled) return;
        const parsed = parseCsv(text);
        setOutputStats({
          rows: parsed.rows.length,
          flaggedForReview: countRowsFlaggedForReview(parsed.rows, parsed.columns),
        });
      })
      .catch(() => {
        if (!cancelled) {
          setOutputStats({ rows: null, flaggedForReview: null });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, outputPath]);

  const humanRequest = useMemo(
    () => extractAuthorWorkflowRequestText(events),
    [events],
  );
  const title = authorSuccessCardTitle({
    templateName: summary.templateName,
    isCustomWorkflowBuild: summary.isCustomWorkflowBuild,
    workflowType: summary.workflowType,
    humanRequest,
  });
  const badgeLabel = authorSuccessBadgeLabel(summary.validationStatus);
  const mainResultLabel = outputPath
    ? humaniseArtifactPathLabel(outputPath, {
        templateName: summary.templateName,
        workflowType: summary.workflowType,
      })
    : "—";
  const summaryReportLabel = summary.workflowReportPath
    ? humaniseArtifactPathLabel(summary.workflowReportPath, {
        templateName: summary.templateName,
        workflowType: summary.workflowType,
      })
    : null;
  const rowsProcessed =
    summary.rowsProcessed !== null
      ? summary.rowsProcessed.toLocaleString()
      : outputStats.rows !== null
        ? outputStats.rows.toLocaleString()
        : "—";
  const rowsFlaggedForReviewCount = outputStats.flaggedForReview;

  return (
    <Card className="border-emerald-200 bg-emerald-50/40">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 text-lg">
          <span>{title}</span>
          <Badge variant="success">{badgeLabel}</Badge>
        </CardTitle>
        <CardDescription>{AUTHOR_SUCCESS_COPY.cardDescription}</CardDescription>
      </CardHeader>
      <CardContent className="pb-5">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <SummaryRow
            label={AUTHOR_SUCCESS_COPY.labelWhatItDoes}
            value={summary.workflowLabel}
          />
          <SummaryRow
            label={AUTHOR_SUCCESS_COPY.labelRowsProcessed}
            value={rowsProcessed}
          />
          <SummaryRow
            label={AUTHOR_SUCCESS_COPY.labelMainResult}
            value={mainResultLabel}
          />
          {summaryReportLabel ? (
            <SummaryRow
              label={AUTHOR_SUCCESS_COPY.labelSummaryReport}
              value={summaryReportLabel}
            />
          ) : null}
          {rowsFlaggedForReviewCount !== null && rowsFlaggedForReviewCount > 0 ? (
            <SummaryRow
              label={AUTHOR_SUCCESS_COPY.labelRowsForReview}
              value={rowsFlaggedForReviewCount.toLocaleString()}
              warn
            />
          ) : null}
        </dl>
      </CardContent>
      <CardFooter className="flex-col items-stretch gap-5 border-emerald-200/80 bg-transparent px-4 pb-4 pt-4">
        <div className="flex w-full flex-wrap items-center gap-3">
          {outputPath ? (
            <DownloadLink
              href={artifactUrl(sessionId, outputPath)}
              download={outputPath.split("/").pop()}
              label={AUTHOR_SUCCESS_COPY.downloadResults}
              primary
            />
          ) : null}
          {summary.workflowReportPath ? (
            <DownloadLink
              href={artifactUrl(sessionId, summary.workflowReportPath)}
              download={summary.workflowReportPath.split("/").pop()}
              label={AUTHOR_SUCCESS_COPY.downloadSummaryReport}
            />
          ) : null}
          <DownloadLink
            href={archiveUrl(sessionId)}
            download={`agentforge-session-${sessionId}.zip`}
            label={AUTHOR_SUCCESS_COPY.downloadFullPackage}
          />
        </div>

        <SmoothCollapse
          title={AUTHOR_SUCCESS_COPY.fileDetailsTitle}
          description={AUTHOR_SUCCESS_COPY.fileDetailsDescription}
          className="w-full border-slate-200 bg-white/80 shadow-none"
        >
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <SummaryRow
              label={AUTHOR_SUCCESS_COPY.labelQualityCheck}
              value={authorSuccessQualityLabel(summary.validationStatus)}
            />
            <SummaryRow
              label={AUTHOR_SUCCESS_COPY.labelSampleFile}
              value={
                summary.inputFilename
                  ? friendlyBasename(summary.inputFilename)
                  : "—"
              }
            />
            <SummaryRow
              label={AUTHOR_SUCCESS_COPY.labelResultsFile}
              value={mainResultLabel}
            />
            <SummaryRow
              label={AUTHOR_SUCCESS_COPY.labelSummaryReport}
              value={
                summary.workflowReportPath
                  ? AUTHOR_SUCCESS_COPY.summaryReportReady
                  : "—"
              }
            />
          </dl>
        </SmoothCollapse>
      </CardFooter>
    </Card>
  );
}

interface SummaryRowProps {
  readonly label: string;
  readonly value: string;
  readonly mono?: boolean;
  readonly warn?: boolean;
}

function SummaryRow({ label, value, mono, warn }: SummaryRowProps) {
  return (
    <div>
      <dt
        className={cn(
          "text-xs font-medium uppercase tracking-wide",
          warn ? "text-amber-700" : "text-slate-500",
        )}
      >
        {label}
      </dt>
      <dd
        className={
          mono
            ? "mt-0.5 break-all font-mono text-xs text-slate-900"
            : cn("mt-0.5", warn ? "font-medium text-amber-900" : "text-slate-900")
        }
      >
        {value}
      </dd>
    </div>
  );
}

function DownloadLink({
  href,
  download,
  label,
  primary = false,
}: {
  readonly href: string;
  readonly download?: string;
  readonly label: string;
  readonly primary?: boolean;
}) {
  return (
    <a
      href={href}
      download={download}
      className={cn(
        "inline-flex h-10 items-center justify-center rounded-md px-4 text-sm font-medium",
        primary
          ? "bg-slate-900 text-white hover:bg-slate-800 active:bg-slate-700"
          : "border border-slate-300 bg-white text-slate-900 hover:bg-slate-50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2",
      )}
    >
      {label}
    </a>
  );
}

// ---------------------------------------------------------------------------
// Derivation
// ---------------------------------------------------------------------------

function deriveSummary(events: ReadonlyArray<WorkspaceEvent>): FinanceSummary {
  let templateName: string | null = null;
  let workflowType: string | null = null;
  let inputFilename: string | null = null;
  let inputFormat: string | null = null;
  let selectedSheet: string | null = null;
  let inputSizeBytes: number | null = null;
  let outputPath: string | null = null;
  let workflowReportPath: string | null = null;
  let rowsProcessed: number | null = null;
  let validationOverall: boolean | null = null;
  const counts = { passed: 0, failed: 0, skipped: 0 };
  const skippedLayers: string[] = [];
  const warnings: string[] = [];
  let pytestPassed: number | null = null;
  let pytestTotal: number | null = null;
  let pytestRan = false;
  let completionVia: string | null = null;
  let modelCallCount = 0;
  const summaryOutputFiles: string[] = [];

  for (const ev of events) {
    if (ev.kind === "file_uploaded") {
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.filename === "string") inputFilename = p.filename;
      if (typeof p.size_bytes === "number") inputSizeBytes = p.size_bytes;
    }
    if (ev.kind === "template_seeded") {
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.template_name === "string") templateName = p.template_name;
    }
    if (ev.kind === "decision_input") {
      const p = ev.payload as Record<string, unknown>;
      if (p.kind === "template_execution") {
        if (typeof p.output_path === "string") outputPath = p.output_path;
      }
      if (p.kind === "custom_workflow_ingest") {
        if (typeof p.upload_format === "string") inputFormat = p.upload_format;
        if (typeof p.selected_sheet === "string") selectedSheet = p.selected_sheet;
        if (typeof p.row_count === "number") rowsProcessed = p.row_count;
      }
      if (p.kind === "custom_workflow_output_contract") {
        if (typeof p.workflow_type === "string") workflowType = p.workflow_type;
        if (Array.isArray(p.summary_output_files)) {
          for (const path of p.summary_output_files) {
            if (typeof path === "string") {
              summaryOutputFiles.push(path);
            } else if (
              path &&
              typeof path === "object" &&
              typeof (path as Record<string, unknown>).path === "string"
            ) {
              summaryOutputFiles.push(String((path as Record<string, unknown>).path));
            }
          }
        }
        if (Array.isArray(p.warnings)) {
          for (const warning of p.warnings) {
            if (typeof warning === "string") warnings.push(warning);
          }
        }
      }
      if (p.kind === "pytest_run") {
        pytestRan = true;
        if (typeof p.passed === "number") pytestPassed = p.passed;
        const failed = typeof p.failed === "number" ? p.failed : 0;
        const errors = typeof p.errors === "number" ? p.errors : 0;
        pytestTotal = (pytestPassed ?? 0) + failed + errors;
      }
    }
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
      if (artifactType === "workflow_report" && path.endsWith(".md")) {
        workflowReportPath = path;
      }
      if (
        (artifactType === "validation_report" ||
          artifactType === "system_validation_report") &&
        path.endsWith(".md")
      ) {
        workflowReportPath ??= path;
      }
    }
    if (ev.kind === "validation_run") {
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.overall_passed === "boolean") {
        validationOverall = p.overall_passed;
      }
      const layers = p.layer_results as Array<Record<string, unknown>> | undefined;
      if (layers) {
        counts.passed = 0;
        counts.failed = 0;
        counts.skipped = 0;
        skippedLayers.length = 0;
        for (const l of layers) {
          if (l.skipped === true) {
            counts.skipped += 1;
            if (typeof l.layer === "string") skippedLayers.push(l.layer);
          } else if (l.passed === true) counts.passed += 1;
          else counts.failed += 1;
        }
      }
    }
    if (ev.kind === "model_called") {
      modelCallCount += 1;
    }
    if (ev.kind === "workflow_completed") {
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.via === "string") {
        completionVia = p.via;
      }
      if (typeof p.workflow_type === "string") workflowType = p.workflow_type;
    }
  }

  let validationStatus: FinanceSummary["validationStatus"] = "unknown";
  if (validationOverall === true) {
    validationStatus = counts.skipped > 0 ? "passed_with_skips" : "passed";
  } else if (validationOverall === false) {
    validationStatus = "failed";
  }

  const isCustomWorkflowBuild =
    completionVia === "ai_authored_workflow_build" && modelCallCount > 0;
  const isPaymentProcessorReconciliation =
    workflowType === "payment_processor_reconciliation";
  const workflowLabel = humaniseWorkflowLabel({
    templateName,
    workflowType,
    isCustomWorkflowBuild,
    humanRequest: extractAuthorWorkflowRequestText(events),
  });

  return {
    workflowLabel,
    templateName,
    workflowType,
    inputFilename,
    inputFormat,
    selectedSheet,
    inputSizeBytes,
    rowsProcessed,
    outputPath,
    validationStatus,
    validationCounts: counts,
    skippedLayers,
    warnings,
    pytestPassed,
    pytestTotal,
    pytestRan,
    completionVia,
    modelCallCount,
    isCustomWorkflowBuild,
    isPaymentProcessorReconciliation,
    summaryOutputFiles,
    workflowReportPath,
  };
}
