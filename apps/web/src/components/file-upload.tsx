"use client";

import { useCallback, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { Button } from "@/components/ui/button";
import { type FileUploadResponse, uploadFile } from "@/lib/api-client";
import { cn } from "@/lib/utils";

/**
 * Drag-and-drop CSV/XLSX uploader. Calls ``POST /sessions/{id}/files``
 * and surfaces the backend's typed error envelope (file_too_large /
 * unsupported_file_type / upload_limit_exceeded) via :class:`ErrorBanner`.
 */
export interface FileUploadProps {
  readonly sessionId: string;
  readonly onUploaded?: (response: FileUploadResponse) => void;
  readonly accept?: string;
}

export function FileUpload({
  sessionId,
  onUploaded,
  accept = ".csv,.xlsx,.xls",
}: FileUploadProps) {
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const handleFile = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        const response = await uploadFile(sessionId, file);
        onUploaded?.(response);
      } catch (err) {
        setError(err);
      } finally {
        setBusy(false);
      }
    },
    [sessionId, onUploaded],
  );

  return (
    <div className="space-y-2">
      <label
        className={cn(
          "block cursor-pointer rounded-md border-2 border-dashed border-slate-300 px-6 py-10 text-center transition",
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
          accept={accept}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        <p className="text-sm font-medium text-slate-700">
          {busy ? "Uploading…" : "Drop a CSV or XLSX file, or click to choose"}
        </p>
        <p className="mt-1 text-xs text-slate-500">Up to 25 MB per file.</p>
      </label>
      {error ? <ErrorBanner error={error} /> : null}
      {busy ? <p className="text-xs text-slate-500">Hashing + storing…</p> : null}
      <noscript>
        <Button variant="secondary" disabled>
          File upload requires JavaScript.
        </Button>
      </noscript>
    </div>
  );
}
