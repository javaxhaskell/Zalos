"use client";

import { Lock } from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { Button } from "@/components/ui/button";
import { type UploadAgentZipResponse, uploadAgentZip } from "@/lib/api-client";
import { cn } from "@/lib/utils";

/**
 * Drag-and-drop ZIP uploader for the repair wizard (final polish round).
 *
 * Complement to the bundled-fixture picker: real customer broken
 * agents arrive as ZIPs of an agent folder + a problem report. The
 * backend extracts via ``POST /sessions/{id}/upload_agent_zip``,
 * defending against zip-slip + per-entry size + aggregate size +
 * file-count caps. Errors surface through :class:`ErrorBanner` with
 * the same typed envelope the rest of the wizard uses.
 */
export interface ZipUploadProps {
  readonly sessionId: string;
  readonly onUploaded?: (response: UploadAgentZipResponse) => void;
  readonly disabled?: boolean;
}

export function ZipUpload({ sessionId, onUploaded, disabled }: ZipUploadProps) {
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [uploaded, setUploaded] = useState<UploadAgentZipResponse | null>(null);

  const handleFile = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        const response = await uploadAgentZip(sessionId, file);
        setUploaded(response);
        onUploaded?.(response);
      } catch (err) {
        setError(err);
      } finally {
        setBusy(false);
      }
    },
    [sessionId, onUploaded],
  );

  if (uploaded) {
    return (
      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm">
        <p className="font-medium text-emerald-900">
          Loaded {uploaded.archive_filename} ✓
        </p>
        <p className="mt-1 text-xs text-emerald-800">
          Extracted {uploaded.files_extracted.length} files into{" "}
          <code className="font-mono">working/</code>
          {uploaded.staged_golden_path ? (
            <>
              {" + staged golden into "}
              <code className="font-mono">{uploaded.staged_golden_path}</code>
            </>
          ) : null}
          .
        </p>
      </div>
    );
  }

  // When the parent flags this control as disabled (e.g. a bundled
  // fixture has already populated working/), show a locked drop zone
  // instead of a near-invisible faded upload area.
  if (disabled) {
    return (
      <div
        className="flex cursor-not-allowed items-center justify-center rounded-md border-2 border-dashed border-slate-200 bg-slate-50 px-6 py-8"
        title="Start a new session to upload a different agent"
        role="status"
        aria-label="ZIP upload locked. Start a new session to upload a different agent."
      >
        <Lock className="size-6 text-slate-400" strokeWidth={1.75} aria-hidden />
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <label
        className={cn(
          "block cursor-pointer rounded-md border-2 border-dashed border-slate-300 px-6 py-8 text-center transition",
          dragOver ? "border-slate-500 bg-slate-50" : "hover:border-slate-400",
          busy ? "pointer-events-none opacity-60" : "",
        )}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files[0];
          if (file) void handleFile(file);
        }}
      >
        <input
          type="file"
          className="hidden"
          accept=".zip,application/zip"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        <p className="text-sm font-medium text-slate-700">
          {busy ? "Extracting ZIP…" : "Drop an agent .zip, or click to choose"}
        </p>
        <p className="mt-1 text-xs text-slate-500">
          Up to 100 files, 10 MB per entry, 100 MB total uncompressed.
        </p>
      </label>
      {error ? <ErrorBanner error={error} /> : null}
      <noscript>
        <Button variant="secondary" disabled>
          ZIP upload requires JavaScript.
        </Button>
      </noscript>
    </div>
  );
}
