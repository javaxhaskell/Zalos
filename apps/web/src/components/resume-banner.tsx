"use client";

import type { Session } from "@/lib/api-client";
import { isFailed } from "@/lib/use-session-state";
import {
  AUTHOR_CUSTOM_WORKFLOW_COPY,
  AUTHOR_INTENT_MISMATCH_COPY,
  REPAIR_CANNOT_REPRODUCE_COPY,
  isAuthorCustomWorkflowNotValidatedSession,
  isAuthorIntentSchemaMismatchSession,
  renderSessionStatus,
} from "@/lib/ux-language";

import { CopySessionIdButton } from "@/components/copyable-session-id";
import { SmoothCollapse } from "@/components/motion/smooth-collapse";

/**
 * Compact return-to-session strip for persisted Author/Repair sessions.
 * Artifacts and events are saved server-side; this is not step-level resume.
 */
export interface ResumeBannerProps {
  readonly session: Session;
  readonly sessionId: string;
}

export function ResumeBanner({ session, sessionId }: ResumeBannerProps) {
  const completed = session.status === "completed";

  return (
    <div className="mb-4">
      <p className="mb-2 text-sm leading-relaxed text-slate-600">
        <span className="font-medium text-slate-800">Returned to saved session.</span>{" "}
        {completed
          ? "Outputs and audit artifacts are preserved below."
          : (
            <>
              <span className="font-medium text-slate-800">Next step:</span>{" "}
              {describeNextStep(session)}
            </>
          )}
      </p>
      <SmoothCollapse
        title="Session details"
        description="Session ID and status"
      >
        <SessionDetailsBody session={session} sessionId={sessionId} />
      </SmoothCollapse>
    </div>
  );
}

function SessionDetailsBody({
  session,
  sessionId,
}: {
  readonly session: Session;
  readonly sessionId: string;
}) {
  return (
    <div className="space-y-2.5">
      <dl className="space-y-2 text-xs">
        <div className="flex items-start justify-between gap-4">
          <dt className="shrink-0 pt-px font-medium text-slate-500">Session ID</dt>
          <dd className="flex min-w-0 items-start justify-end gap-0.5 text-right">
            <span className="break-all font-mono text-slate-900">{sessionId}</span>
            <CopySessionIdButton sessionId={sessionId} className="mt-px" />
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt className="shrink-0 font-medium text-slate-500">Status</dt>
          <dd className="text-right text-slate-900">{renderSessionStatus(session.status)}</dd>
        </div>
      </dl>
    </div>
  );
}

export { SessionDetailsBody };

function describeNextStep(session: Session): string {
  if (session.status === "created") {
    return session.workflow === "author"
      ? "Upload a sample file and describe the workflow."
      : "Choose a sample agent or upload your own file.";
  }
  if (session.status === "running") {
    return "Watch progress on this page while the run continues.";
  }
  if (session.status === "paused_approval") {
    return "Review the pending approval below.";
  }
  if (session.status === "paused_user") {
    return "Answer the agent's question below.";
  }
  if (isFailed(session.status)) {
    if (isAuthorIntentSchemaMismatchSession(session)) {
      return AUTHOR_INTENT_MISMATCH_COPY.nextStepsFallback;
    }
    if (isAuthorCustomWorkflowNotValidatedSession(session)) {
      return AUTHOR_CUSTOM_WORKFLOW_COPY.nextStepsFallback;
    }
    if (session.workflow === "repair" && session.terminal_error_code === "repair_cannot_reproduce") {
      return REPAIR_CANNOT_REPRODUCE_COPY.nextStep;
    }
    return "Review what went wrong below. Use Retry with same inputs for a full rerun, or start a new session.";
  }
  return "Pick up where you left off on this page.";
}
