"use client";

import type { TimelineStage } from "@/lib/workflow-timeline";
import { cn } from "@/lib/utils";

export interface WorkflowTimelineProps {
  readonly stages: ReadonlyArray<TimelineStage>;
  readonly title?: string;
  readonly className?: string;
}

function StageIcon({ state }: { readonly state: TimelineStage["state"] }) {
  if (state === "done") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500 text-[11px] font-bold text-white shadow-[0_0_8px_rgba(16,185,129,0.35)]">
        ✓
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative flex h-6 w-6 items-center justify-center">
        <span className="absolute h-6 w-6 rounded-full bg-emerald-400/30 animate-ping" />
        <span className="relative h-3 w-3 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.55)]" />
      </span>
    );
  }
  return <span className="h-3 w-3 rounded-full bg-slate-300" />;
}

/**
 * Vertical workflow rail with uniform row sizing.
 */
export function WorkflowTimeline({ stages, title, className }: WorkflowTimelineProps) {
  return (
    <div className={cn("rounded-xl border border-slate-200 bg-white p-6 shadow-sm", className)}>
      {title ? (
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      ) : null}
      <ol
        className={cn("relative", title ? "mt-6" : undefined)}
        aria-label="Workflow progress"
      >
        {stages.map((stage, index) => (
          <li
            key={stage.id}
            className="relative flex min-h-[3.5rem] items-center gap-4 py-3 pl-1"
          >
            {index < stages.length - 1 ? (
              <span
                className="absolute left-[0.6rem] top-[2.75rem] h-[calc(100%-0.75rem)] w-px bg-slate-200"
                aria-hidden="true"
              />
            ) : null}
            <span className="relative z-[1] flex h-6 w-6 shrink-0 items-center justify-center">
              <StageIcon state={stage.state} />
            </span>
            <span
              className={cn(
                "text-sm leading-5",
                stage.state === "pending"
                  ? "text-slate-400"
                  : stage.state === "active"
                    ? "font-medium text-slate-900"
                    : "text-slate-700",
              )}
            >
              {stage.label}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
