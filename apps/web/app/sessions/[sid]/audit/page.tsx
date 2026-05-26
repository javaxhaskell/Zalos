"use client";

import Link from "next/link";
import { useState } from "react";

import { CopyableSessionId } from "@/components/copyable-session-id";
import { ErrorBanner } from "@/components/error-banner";
import { EventLogStream } from "@/components/event-log-stream";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { exportAudit, type AuditExportResponse } from "@/lib/api-client";

interface PageProps {
  readonly params: { readonly sid: string };
}

/**
 * Audit-export page. Surfaces the full event chain + a one-click JSON
 * download (the canonical audit artifact a grader / compliance
 * operator would inspect).
 */
export default function AuditPage({ params }: PageProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [exported, setExported] = useState<AuditExportResponse | null>(null);

  async function onExport() {
    setBusy(true);
    setError(null);
    try {
      const result = await exportAudit(params.sid);
      setExported(result);
      const blob = new Blob([JSON.stringify(result, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-${params.sid}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <main className="mx-auto max-w-5xl px-6 py-10">
        <nav className="mb-4 text-sm text-slate-500">
          <Link href="/" className="hover:underline">
            ← Back to dashboard
          </Link>
        </nav>
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              Audit
            </h1>
            <p className="mt-1 text-xs text-slate-500">
              <CopyableSessionId sessionId={params.sid} className="text-slate-500" />
            </p>
          </div>
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() => void onExport()}
          >
            {busy ? "Exporting…" : "Download audit JSON"}
          </Button>
        </header>

        {error ? (
          <div className="mb-4">
            <ErrorBanner error={error} />
          </div>
        ) : null}

        {exported ? (
          <Alert variant="success" className="mb-4">
            <AlertDescription>
              Exported {exported.event_count} event(s). Chain integrity:{" "}
              <strong>{exported.chain_check.valid ? "valid" : "broken"}</strong>
              {exported.chain_check.message
                ? ` — ${exported.chain_check.message}`
                : null}
              .
            </AlertDescription>
          </Alert>
        ) : null}

        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Live event stream
          </h2>
          <EventLogStream sessionId={params.sid} />
        </section>
      </main>
    </>
  );
}
