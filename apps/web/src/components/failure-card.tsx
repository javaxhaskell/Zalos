"use client";

import { useMemo } from "react";

import { CopyableSessionId } from "@/components/copyable-session-id";
import { FailureMitigationActions } from "@/lib/failure-mitigation-actions";
import { SmoothCollapse } from "@/components/motion/smooth-collapse";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Session, WorkspaceEvent } from "@/lib/api-client";
import {
  REPAIR_CANNOT_REPRODUCE_COPY,
  humaniseTerminalFailure,
  humanizeEvidenceLabel,
  isRepairCannotReproduceSession,
  renderSessionStatus,
} from "@/lib/ux-language";

/**
 * Replaces the generic CompletedStage banner when a session ends in a
 * ``failed_*`` terminal status (BP11). Surfaces:
 *
 *   * Plain-English headline and explanation for finance users.
 *   * A suggested next action keyed off the error code.
 *   * Optional discovered issue when the workflow surfaced one.
 *   * Collapsed technical details (raw logs, error codes).
 *
 * The card is intentionally calm — failure states in the demo should
 * feel diagnosable, not catastrophic.
 */
export interface FailureCardProps {
  readonly sessionId: string;
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

export function FailureCard({ sessionId, session, events }: FailureCardProps) {
  const mitigation = session.failure_mitigation;
  const details = useMemo(
    () =>
      humaniseTerminalFailure({
        errorCode: session.terminal_error_code,
        sessionStatus: session.status,
        events,
      }),
    [session, events],
  );

  const headline = mitigation?.user_title ?? details.headline;
  const message = mitigation?.user_summary ?? details.message;
  const discoveredIssue = mitigation?.what_we_found ?? details.discoveredIssue;
  const nextStep = mitigation ? null : details.nextStep;

  if (isRepairCannotReproduceSession(session)) {
    return (
      <RepairCannotReproduceResult
        sessionId={sessionId}
        session={session}
        details={details}
      />
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{headline}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-slate-700">
        <p>{message}</p>
        {discoveredIssue ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-800">
              What we found
            </h3>
            <p className="mt-1 text-sm text-amber-950">{discoveredIssue}</p>
          </div>
        ) : null}
        {mitigation?.evidence_items?.length ? (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Evidence preserved
            </h3>
            <ul className="mt-1 list-inside list-disc text-sm text-slate-800">
              {mitigation.evidence_items.slice(0, 5).map((item) => (
                <li key={item}>{humanizeEvidenceLabel(item)}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {nextStep ? (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Next step
            </h3>
            <p className="mt-1 text-sm text-slate-800">{nextStep}</p>
          </div>
        ) : null}
        {details.rawTechnicalDetail || session.terminal_error_code ? (
          <div id="technical-details">
            <SmoothCollapse
              title="Technical details"
              description="Error codes, file paths, and detailed logs for engineers."
              className="border-slate-200 bg-slate-50/80 shadow-none"
              sectionId="technical-details"
            >
            {session.terminal_error_code ? (
              <p className="mb-2 font-mono text-xs text-slate-600">
                Error code: {session.terminal_error_code}
              </p>
            ) : null}
            {details.technicalSummary ? (
              <p className="mb-3 text-sm text-slate-800">{details.technicalSummary}</p>
            ) : null}
            {details.rawTechnicalDetail ? (
              <>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Detailed log
                </p>
                <p className="font-mono text-xs text-slate-700 break-all whitespace-pre-wrap">
                  {details.rawTechnicalDetail}
                </p>
              </>
            ) : null}
          </SmoothCollapse>
          </div>
        ) : null}
        <FailureMitigationActions sessionId={sessionId} session={session} />
      </CardContent>
    </Card>
  );
}

function RepairCannotReproduceResult({
  sessionId,
  session,
  details,
}: {
  readonly sessionId: string;
  readonly session: Session;
  readonly details: ReturnType<typeof humaniseTerminalFailure>;
}) {
  const statusLabel = renderSessionStatus(session.status);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Repair result</CardTitle>
        <p className="text-base font-medium text-slate-900">
          {REPAIR_CANNOT_REPRODUCE_COPY.title}
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-slate-700">
        <dl className="space-y-3">
          <ResultRow label="Status" value={statusLabel} />
          <ResultRow label="Explanation" value={details.message} />
          {details.discoveredIssue ? (
            <ResultRow label="Discovered issue" value={details.discoveredIssue} />
          ) : null}
          {details.nextStep ? (
            <ResultRow label="Next step" value={details.nextStep} />
          ) : null}
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

        <FailureMitigationActions sessionId={sessionId} session={session} />
      </CardContent>
    </Card>
  );
}

function ResultRow({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 text-slate-900">{value}</dd>
    </div>
  );
}
