"use client";

import { useEffect, useState } from "react";

import { CollapsibleSection } from "@/components/collapsible-section";
import { AnimatedSection } from "@/components/motion";
import { EventLogTable } from "@/components/event-log-table";
import type { Session, WorkspaceEvent } from "@/lib/api-client";
import { deriveActivityMilestones } from "@/lib/activity-milestones";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";

export interface ActivityPanelProps {
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly session?: Session | null;
  readonly compact?: boolean;
}

/**
 * Milestone timeline by default; full technical log behind a disclosure.
 */
export function ActivityPanel({ events, session, compact = false }: ActivityPanelProps) {
  const milestones = deriveActivityMilestones(events, session ?? undefined);
  const reduced = usePrefersReducedMotion();
  const [revealed, setRevealed] = useState(reduced);

  useEffect(() => {
    if (reduced) {
      setRevealed(true);
      return;
    }
    setRevealed(false);
    const timer = window.setTimeout(() => setRevealed(true), 0);
    return () => window.clearTimeout(timer);
  }, [milestones.length, reduced]);

  if (compact) {
    return null;
  }

  return (
    <AnimatedSection index={0} className="space-y-4">
      <section className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">What happened</h2>
        <p className="mt-0.5 text-xs text-slate-600">
          Key steps from this workflow. Expand below for the full activity log.
        </p>
        {milestones.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">Waiting for activity…</p>
        ) : (
          <ol className="mt-3 space-y-2 border-l-2 border-slate-200 pl-4">
            {milestones.map((m, i) => (
              <li
                key={m.id}
                className={cn(
                  "relative text-sm text-slate-800",
                  !reduced &&
                    "transition-[opacity,transform] duration-200 ease-out",
                  !reduced && revealed
                    ? "translate-y-0 opacity-100"
                    : !reduced
                      ? "translate-y-1 opacity-0"
                      : undefined,
                )}
                style={
                  !reduced && revealed
                    ? { transitionDelay: `${i * 40}ms` }
                    : undefined
                }
              >
                <span className="absolute -left-[1.35rem] top-1.5 h-2 w-2 rounded-full bg-slate-400" />
                {m.label}
              </li>
            ))}
          </ol>
        )}
      </section>

      <CollapsibleSection
        title="Show full technical activity log"
        description="Step-by-step record of what happened in this session."
      >
        <EventLogTable events={events} />
      </CollapsibleSection>
    </AnimatedSection>
  );
}
