"use client";

import { useState } from "react";

import { FadeIn } from "@/components/motion/fade-in";
import { SkeletonTableRows } from "@/components/motion";
import { MarkdownViewer } from "@/components/markdown-viewer";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { archiveUrl, artifactUrl, type WorkspaceEvent } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const DOWNLOAD_BUTTON_CLASSES = cn(
  "inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium",
  "bg-slate-900 text-white hover:bg-slate-800 active:bg-slate-700",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2",
  "transition-colors",
);

/**
 * Card shown in the wizard's CompletedStage (BP10a). Surfaces:
 *
 *   * A primary "Download archive (.zip)" button hitting
 *     ``GET /sessions/{id}/archive.zip`` — lazily built server-side
 *     if the agent didn't call ``archive_workspace``.
 *   * The list of artifacts the agent produced (from
 *     ``artifact_generated`` events), with "Open" links to the
 *     individual ``/artifacts/{path}`` endpoint.
 *   * An inline :class:`MarkdownViewer` for ``.md`` artifacts so the
 *     finance user sees the validation / repair report without
 *     having to download anything first.
 */
export interface ArtifactDownloadPanelProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

interface ArtifactEntry {
  readonly path: string;
  readonly type: string;
  readonly sizeBytes: number | null;
}

export function ArtifactDownloadPanel({
  sessionId,
  events,
}: ArtifactDownloadPanelProps) {
  const artifacts = extractArtifacts(events);
  const reports = artifacts.filter((a) => a.path.endsWith(".md"));
  const [openReportPath, setOpenReportPath] = useState<string | null>(
    reports[0]?.path ?? null,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Artifacts</CardTitle>
        <CardDescription>
          Everything the agent produced in this session. The archive
          bundles them all into a single .zip you can attach to a
          ticket or hand off for audit.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <a
            href={archiveUrl(sessionId)}
            download={`agentforge-session-${sessionId}.zip`}
            className={DOWNLOAD_BUTTON_CLASSES}
          >
            Download archive (.zip)
          </a>
          <span className="text-xs text-slate-500">
            Builds on demand if the agent didn&apos;t already produce one.
          </span>
        </div>

        {artifacts.length === 0 ? (
          events.length === 0 ? (
            <SkeletonTableRows rows={3} cols={3} />
          ) : (
            <p className="text-sm text-slate-500">
              No artifacts recorded yet. Archive will include the events
              log, manifest, and any reports the agent produces.
            </p>
          )
        ) : (
          <ul className="space-y-1 text-sm">
            {artifacts.map((a) => (
              <li
                key={a.path}
                className="flex items-baseline gap-3 rounded-md border border-slate-200 px-3 py-2"
              >
                <Badge variant="neutral">{a.type}</Badge>
                <code className="flex-1 font-mono text-xs text-slate-700">
                  {a.path}
                </code>
                {a.sizeBytes !== null ? (
                  <span className="text-xs text-slate-500">
                    {formatSize(a.sizeBytes)}
                  </span>
                ) : null}
                {a.path.endsWith(".md") ? (
                  <button
                    type="button"
                    className="text-xs font-medium text-slate-700 underline-offset-2 hover:underline"
                    onClick={() =>
                      setOpenReportPath(
                        openReportPath === a.path ? null : a.path,
                      )
                    }
                  >
                    {openReportPath === a.path ? "Hide" : "View"}
                  </button>
                ) : null}
                <a
                  href={artifactUrl(sessionId, a.path)}
                  className="text-xs font-medium text-slate-700 underline-offset-2 hover:underline"
                  download={a.path.split("/").pop()}
                >
                  Download
                </a>
              </li>
            ))}
          </ul>
        )}

        {openReportPath ? (
          <FadeIn>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              {openReportPath}
            </h3>
            <MarkdownViewer
              sessionId={sessionId}
              relativePath={openReportPath}
            />
          </FadeIn>
        ) : null}
      </CardContent>
    </Card>
  );
}

function extractArtifacts(
  events: ReadonlyArray<WorkspaceEvent>,
): ArtifactEntry[] {
  const out: ArtifactEntry[] = [];
  const seen = new Set<string>();
  for (const ev of events) {
    if (ev.kind !== "artifact_generated") continue;
    const payload = ev.payload as Record<string, unknown>;
    const path = payload.path as string | undefined;
    if (!path || seen.has(path)) continue;
    // Skip the archive itself in the artifact list — it's the
    // primary Download button at the top.
    if (path.endsWith("archive.zip")) continue;
    seen.add(path);
    out.push({
      path,
      type: (payload.artifact_type as string) ?? "artifact",
      sizeBytes:
        typeof payload.size_bytes === "number"
          ? (payload.size_bytes as number)
          : null,
    });
  }
  return out;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}
