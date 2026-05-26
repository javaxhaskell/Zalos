"use client";

import { useEffect, useMemo, useState } from "react";

import {
  DataPreviewTable,
  DEFAULT_PREVIEW_MAX_ROWS,
  PreviewOverviewPanel,
  parseCsv,
  previewTableSectionLabel,
  previewViewTitle,
  rowsToParsedCsv,
  type ParsedCsv,
  type PreviewViewMode,
} from "@/components/data-preview-table";
import { SkeletonTableRows } from "@/components/motion";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { fetchArtifactText, type WorkspaceEvent } from "@/lib/api-client";
import { isTerminal } from "@/lib/use-session-state";

export interface InputPreviewCardProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly sessionStatus?: string;
}

interface IngestSnapshot {
  readonly columns: string[];
  readonly rows: Array<Record<string, string>>;
  readonly filePath: string;
  readonly rowCount: number;
}

/**
 * Preview of the uploaded input CSV/XLSX — first rows surfaced after
 * ingest so finance users can confirm the workflow read the right file
 * while the agent is still building.
 */
export function InputPreviewCard({
  sessionId,
  events,
  sessionStatus,
}: InputPreviewCardProps) {
  const ingest = useMemo(() => findLatestIngestSnapshot(events), [events]);
  const uploadPath = useMemo(() => findUploadedInputPath(events), [events]);
  const filePath = ingest?.filePath ?? uploadPath;

  const [fetched, setFetched] = useState<ParsedCsv | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode] = useState<PreviewViewMode>("preview");

  const inlineParsed = useMemo(() => {
    if (!ingest || ingest.rows.length === 0) return null;
    return rowsToParsedCsv(ingest.columns, ingest.rows);
  }, [ingest]);

  const totalRows =
    ingest?.rowCount ?? fetched?.rows.length ?? inlineParsed?.rows.length ?? null;
  const currentRowCount = (fetched ?? inlineParsed)?.rows.length ?? 0;
  const needsFullFile =
    viewMode === "overview" &&
    filePath !== null &&
    (totalRows === null || currentRowCount < totalRows);
  const parsed =
    viewMode === "overview" && fetched !== null
      ? fetched
      : inlineParsed ?? fetched;
  const hasPreviewData = (parsed?.rows.length ?? 0) > 0;
  const showPreviewError =
    error !== null &&
    !loading &&
    !hasPreviewData &&
    isTerminal(sessionStatus);

  useEffect(() => {
    if (!needsFullFile || !filePath) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchArtifactText(sessionId, filePath)
      .then((text) => {
        if (cancelled) return;
        setFetched(parseCsv(text));
      })
      .catch((e) => {
        if (cancelled) return;
        // Ingest sample rows are enough for preview while the session runs.
        if (!inlineParsed) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, filePath, needsFullFile, inlineParsed]);

  useEffect(() => {
    if (inlineParsed || !filePath) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchArtifactText(sessionId, filePath)
      .then((text) => {
        if (cancelled) return;
        setFetched(parseCsv(text));
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
  }, [sessionId, filePath, inlineParsed]);

  if (!filePath && !ingest) return null;

  return (
    <Card className="border-slate-200/90 shadow-sm">
      <CardHeader className="border-b border-slate-100 bg-white pb-4">
        <h3 className="text-lg font-semibold tracking-tight text-slate-900">
          {previewViewTitle(viewMode)}
        </h3>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div aria-busy="true" aria-label="Loading input preview">
            <SkeletonTableRows rows={6} cols={4} />
          </div>
        ) : null}
        {showPreviewError ? (
          <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            We could not load a preview of the uploaded file.
          </p>
        ) : null}
        {parsed ? (
          <PreviewOverviewPanel>
            <DataPreviewTable
              parsed={parsed}
              maxRows={
                viewMode === "overview"
                  ? parsed.rows.length
                  : DEFAULT_PREVIEW_MAX_ROWS
              }
              totalRowHint={totalRows}
              sectionLabel={previewTableSectionLabel(viewMode === "overview")}
              ariaLabel={
                viewMode === "overview" ? "All input rows" : "Input rows"
              }
            />
          </PreviewOverviewPanel>
        ) : null}
      </CardContent>
    </Card>
  );
}

function findLatestIngestSnapshot(
  events: ReadonlyArray<WorkspaceEvent>,
): IngestSnapshot | null {
  let latest: IngestSnapshot | null = null;
  for (const ev of events) {
    if (ev.kind !== "decision_input") continue;
    const p = ev.payload as Record<string, unknown>;
    if (p.kind !== "custom_workflow_ingest") continue;

    const columns = Array.isArray(p.columns)
      ? p.columns.filter((col): col is string => typeof col === "string")
      : [];
    const sampleRows = Array.isArray(p.sample_rows) ? p.sample_rows : [];
    const rows: Array<Record<string, string>> = [];
    for (const raw of sampleRows) {
      if (!raw || typeof raw !== "object") continue;
      const record = raw as Record<string, unknown>;
      const row: Record<string, string> = {};
      for (const col of columns) {
        const value = record[col];
        row[col] = value == null ? "" : String(value);
      }
      rows.push(row);
    }

    const filePath =
      typeof p.normalized_input_path === "string"
        ? p.normalized_input_path
        : typeof p.original_upload_path === "string"
          ? p.original_upload_path
          : "uploads/input.csv";
    const rowCount =
      typeof p.row_count === "number" ? p.row_count : rows.length;

    latest = { columns, rows, filePath, rowCount };
  }
  return latest;
}

function findUploadedInputPath(
  events: ReadonlyArray<WorkspaceEvent>,
): string | null {
  let path: string | null = null;
  for (const ev of events) {
    if (ev.kind === "file_uploaded") {
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.relative_path === "string") {
        path = p.relative_path;
      } else if (typeof p.filename === "string") {
        path = `uploads/${p.filename}`;
      }
    }
  }
  return path;
}
