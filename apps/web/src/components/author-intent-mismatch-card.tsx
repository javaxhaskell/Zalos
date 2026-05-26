"use client";

import Link from "next/link";
import { useMemo } from "react";

import { CopyableSessionId } from "@/components/copyable-session-id";
import { SmoothCollapse } from "@/components/motion/smooth-collapse";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Session, WorkspaceEvent } from "@/lib/api-client";
import {
  AUTHOR_CUSTOM_WORKFLOW_COPY,
  AUTHOR_INTENT_MISMATCH_COPY,
  extractAuthorCustomWorkflowNotValidated,
  extractAuthorIntentMismatch,
  isAuthorCustomWorkflowNotValidatedSession,
  isAuthorStoppedBeforeGenerationSession,
  renderSessionStatus,
} from "@/lib/ux-language";

export interface AuthorIntentMismatchCardProps {
  readonly sessionId: string;
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

export function AuthorIntentMismatchCard({
  sessionId,
  session,
  events,
}: AuthorIntentMismatchCardProps) {
  const mismatch = useMemo(() => extractAuthorIntentMismatch(events), [events]);
  const customStop = useMemo(
    () => extractAuthorCustomWorkflowNotValidated(events),
    [events],
  );
  const isCustomStop = isAuthorCustomWorkflowNotValidatedSession(session);
  const copy = isCustomStop ? AUTHOR_CUSTOM_WORKFLOW_COPY : AUTHOR_INTENT_MISMATCH_COPY;
  const missingColumns = useMemo(
    () => formatMissingColumns(mismatch?.missingColumns ?? []),
    [mismatch],
  );
  const detectedColumns = customStop?.detectedColumns ?? [];
  const statusLabel = renderSessionStatus(session.status);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{copy.sectionTitle}</CardTitle>
        <p className="text-base font-medium text-slate-900">{copy.title}</p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-slate-700">
        <dl className="space-y-3">
          <Row label="Status" value={statusLabel} />
          <Row
            label="Explanation"
            value={
              isCustomStop
                ? customStop?.explanation ?? AUTHOR_CUSTOM_WORKFLOW_COPY.explanationFallback
                : mismatch?.explanation ?? AUTHOR_INTENT_MISMATCH_COPY.explanationFallback
            }
          />
          {(isCustomStop ? customStop?.detectedFileType : mismatch?.detectedFileType) ? (
            <Row
              label="Detected file type"
              value={humaniseFileType(
                String(
                  (isCustomStop
                    ? customStop?.detectedFileType
                    : mismatch?.detectedFileType) ?? "",
                ),
              )}
            />
          ) : null}
          {(isCustomStop ? customStop?.requestedWorkflow : mismatch?.requestedWorkflow) ? (
            <Row
              label="Requested workflow type"
              value={humaniseWorkflowType(
                String(
                  (isCustomStop
                    ? customStop?.requestedWorkflow
                    : mismatch?.requestedWorkflow) ?? "",
                ),
              )}
            />
          ) : null}
          {isCustomStop && customStop?.uploadFormat ? (
            <Row label="Upload format" value={customStop.uploadFormat.toUpperCase()} />
          ) : null}
          {isCustomStop && detectedColumns.length ? (
            <Row
              label="Detected columns"
              value={detectedColumns.join(", ")}
            />
          ) : null}
          {!isCustomStop && missingColumns.length ? (
            <Row
              label="Missing required columns"
              value={missingColumns.join(", ")}
            />
          ) : null}
          <Row
            label="Next step"
            value={
              isCustomStop
                ? customStop?.nextSteps ?? AUTHOR_CUSTOM_WORKFLOW_COPY.nextStepsFallback
                : mismatch?.nextSteps ?? AUTHOR_INTENT_MISMATCH_COPY.nextStepsFallback
            }
          />
        </dl>

        <SmoothCollapse
          title="Technical details"
          description="Optional detail about this session."
          className="border-slate-200 bg-slate-50/80 shadow-none"
        >
          <dl className="space-y-1 text-xs text-slate-500">
            <div>
              <dt className="inline font-medium text-slate-600">Session ID: </dt>
              <dd className="inline">
                <CopyableSessionId sessionId={sessionId} inline />
              </dd>
            </div>
            {session.terminal_error_code ? (
              <div>
                <dt className="inline font-medium text-slate-600">Error code: </dt>
                <dd className="inline font-mono">{session.terminal_error_code}</dd>
              </div>
            ) : null}
          </dl>
        </SmoothCollapse>

        <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-3 text-xs">
          <Link
            href={`/sessions/${sessionId}/audit`}
            className="font-medium text-slate-700 underline-offset-2 hover:underline"
          >
            Open full audit →
          </Link>
          <Link
            href="/"
            className="font-medium text-slate-700 underline-offset-2 hover:underline"
          >
            Start a new session
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export function shouldShowAuthorIntentMismatchCard(session: Session): boolean {
  return isAuthorStoppedBeforeGenerationSession(session);
}

function Row({
  label,
  value,
  mono,
}: {
  readonly label: string;
  readonly value: string;
  readonly mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd
        className={
          mono
            ? "mt-1 font-mono text-xs text-slate-900"
            : "mt-1 text-slate-900"
        }
      >
        {value}
      </dd>
    </div>
  );
}

function formatMissingColumns(columns: readonly string[]): string[] {
  return columns.filter((column) => column !== "amount");
}

function humaniseFileType(value: string): string {
  const map: Record<string, string> = {
    bank_transactions: "Bank transactions",
    invoices: "Invoices",
    payment_processor_reconciliation: "Payment processor reconciliation",
    unknown: "Unknown",
  };
  return map[value] ?? value.replaceAll("_", " ");
}

function humaniseWorkflowType(value: string): string {
  const map: Record<string, string> = {
    bank_categorisation: "Bank Transaction Categorisation",
    expense_exception_review: "Expense Exception Review",
    invoice_aging: "Invoice aging",
    other_finance: "Other finance workflow",
    unspecified: "Unspecified",
  };
  return map[value] ?? value.replaceAll("_", " ");
}
