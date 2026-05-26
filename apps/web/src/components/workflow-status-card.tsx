"use client";

import Link from "next/link";

import {
  LiveProcessBadge,
  sessionStatusToTone,
  type LiveProcessTone,
} from "@/components/live-process-badge";
import { WorkflowTimeline } from "@/components/workflow-timeline";
import type { Session, WorkspaceEvent } from "@/lib/api-client";
import {
  deriveTimelineStages,
  deriveWorkflowStatusLabel,
  type WorkflowSurface,
} from "@/lib/workflow-timeline";

export interface WorkflowStatusCardProps {
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly workflow: WorkflowSurface;
  readonly workItemTitle?: string;
  readonly primaryAction?: React.ReactNode;
}

function toneForSession(session: Pick<Session, "status">): LiveProcessTone {
  return sessionStatusToTone(session.status);
}

function cardTitle(session: Pick<Session, "status">, workflow: WorkflowSurface): string {
  if (session.status === "running") {
    return workflow === "repair" ? "Repairing your agent…" : "Building your agent…";
  }
  if (session.status.startsWith("paused_")) return "Workflow saved";
  if (session.status === "completed") {
    return workflow === "repair" ? "Repair complete" : "Workflow complete";
  }
  if (session.status.startsWith("failed_")) {
    return workflow === "repair" ? "Repair needs review" : "Workflow needs review";
  }
  return "Workflow status";
}

/**
 * Primary finance-user status surface for active sessions.
 */
export function WorkflowStatusCard({
  session,
  events,
  workflow,
  workItemTitle,
  primaryAction,
}: WorkflowStatusCardProps) {
  const stages = deriveTimelineStages(workflow, events);
  const statusLabel = deriveWorkflowStatusLabel(session);
  const tone = toneForSession(session);
  const showStatusBadge = session.status !== "running";

  return (
    <section className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="min-w-0 flex-1">
            {workItemTitle ? (
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {workItemTitle}
              </p>
            ) : null}
            <h2 className="mt-2 text-lg font-semibold text-slate-950">
              {cardTitle(session, workflow)}
            </h2>
            {showStatusBadge ? (
              <div className="mt-4">
                <LiveProcessBadge tone={tone} label={statusLabel} />
              </div>
            ) : null}
          </div>
          {primaryAction ? <div className="shrink-0">{primaryAction}</div> : null}
        </div>
      </div>

      <WorkflowTimeline
        title={session.status === "running" ? "Progress" : "Workflow progress"}
        stages={stages}
      />
    </section>
  );
}

export interface RunningProcessCardProps {
  readonly session: Pick<Session, "id" | "workflow" | "status">;
  readonly workItem: string;
}

/**
 * Dashboard card for an in-flight authoring or repair session.
 */
export function RunningProcessCard({
  session,
  workItem,
}: RunningProcessCardProps) {
  const tone = toneForSession(session);

  return (
    <div className="flex flex-wrap items-center justify-between gap-5 rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm">
      <div className="min-w-0 flex-1 space-y-3">
        <p className="truncate text-sm font-semibold text-slate-900">{workItem}</p>
        <LiveProcessBadge tone={tone} label={deriveWorkflowStatusLabel(session)} />
      </div>
      <Link
        href={`/${session.workflow}/${session.id}`}
        className="inline-flex h-10 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white transition-colors hover:bg-slate-800"
      >
        Open workflow
      </Link>
    </div>
  );
}
