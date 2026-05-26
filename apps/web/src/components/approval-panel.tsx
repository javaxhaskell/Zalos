"use client";

import { useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { approve, reject } from "@/lib/api-client";

/**
 * Approval gate UI (ADR-0006).
 *
 * Business-level summary primary; technical detail (the diff) collapsed
 * behind a click. **Approve** is a single button; **Decline** requires
 * a free-text reason (per the ADR). All state lives in the backend —
 * this component just dispatches the HTTP call and surfaces the
 * resulting envelope.
 */
export interface ApprovalPanelProps {
  readonly sessionId: string;
  readonly requestId: string;
  readonly toolName: string;
  readonly businessSummary: string;
  readonly diff?: string | null;
  readonly onDecided?: (status: "granted" | "declined") => void;
}

export function ApprovalPanel({
  sessionId,
  requestId,
  toolName,
  businessSummary,
  diff,
  onDecided,
}: ApprovalPanelProps) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function onApprove() {
    setBusy("approve");
    setError(null);
    try {
      await approve(sessionId, requestId);
      onDecided?.("granted");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function onDecline() {
    if (reason.trim().length === 0) {
      setError(new Error("Please give a short reason so the agent can adjust."));
      return;
    }
    setBusy("decline");
    setError(null);
    try {
      await reject(sessionId, requestId, reason.trim());
      onDecided?.("declined");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Waiting on your approval</CardTitle>
        <CardDescription>
          The agent wants to run <code>{toolName}</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-800">{businessSummary}</p>
        {diff ? (
          <details className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium text-slate-700">
              Show technical change
            </summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs text-slate-800">
              {diff}
            </pre>
          </details>
        ) : null}
        {error ? <ErrorBanner error={error} /> : null}
        <div>
          <label
            htmlFor="approval-reason"
            className="block text-xs font-medium text-slate-700"
          >
            Reason (required to decline)
          </label>
          <textarea
            id="approval-reason"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g., I want to review the data file first"
          />
        </div>
      </CardContent>
      <CardFooter className="justify-end gap-3">
        <Button
          type="button"
          variant="destructive"
          disabled={busy !== null}
          onClick={onDecline}
        >
          {busy === "decline" ? "Declining…" : "Decline"}
        </Button>
        <Button
          type="button"
          variant="primary"
          disabled={busy !== null}
          onClick={onApprove}
        >
          {busy === "approve" ? "Approving…" : "Approve"}
        </Button>
      </CardFooter>
    </Card>
  );
}
