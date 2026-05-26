"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import type { WorkspaceEvent } from "@/lib/api-client";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";
import { humaniseEventKind, describeEventTechnicalDetail, renderEventMessage } from "@/lib/ux-language";

export interface EventLogTableProps {
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

export function EventLogTable({ events }: EventLogTableProps) {
  const reduced = usePrefersReducedMotion();
  const seenRef = useRef(new Set<string>());
  const [highlightIds, setHighlightIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const visibleEvents = useMemo(
    () => dedupeConsecutiveDisplayedEvents(events),
    [events],
  );

  useEffect(() => {
    const fresh: string[] = [];
    for (const ev of visibleEvents) {
      if (!ev.id || seenRef.current.has(ev.id)) continue;
      seenRef.current.add(ev.id);
      fresh.push(ev.id);
    }
    if (fresh.length === 0) return;
    setHighlightIds(new Set(fresh));
    const timer = window.setTimeout(() => setHighlightIds(new Set()), 1_000);
    return () => window.clearTimeout(timer);
  }, [visibleEvents]);

  if (visibleEvents.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500">
        No activity recorded yet.
      </p>
    );
  }

  return (
    <Table>
      <THead>
        <TR>
          <TH className="w-28">When</TH>
          <TH className="w-44">Activity</TH>
          <TH>What happened</TH>
        </TR>
      </THead>
      <TBody>
        {visibleEvents.map((event) => {
          const payload = event.payload as Record<string, unknown>;
          const isNew = event.id ? highlightIds.has(event.id) : false;
          const activityLabel = humaniseEventKind(event.kind, payload);
          const technicalDetail = describeEventTechnicalDetail(event.kind, payload);
          return (
            <TR
              key={event.id}
              className={cn(
                !reduced && isNew && "animate-row-in animate-row-highlight",
              )}
              style={
                !reduced && isNew
                  ? { animationFillMode: "backwards, forwards" }
                  : undefined
              }
            >
              <TD className="font-mono text-xs text-slate-500">
                {formatTime(event.ts)}
              </TD>
              <TD>
                <ActivityBadge
                  label={activityLabel}
                  technicalDetail={technicalDetail}
                  variant={badgeVariant(event.kind, payload)}
                />
              </TD>
              <TD className="text-sm text-slate-800">
                {renderEventMessage(event.kind, payload, events)}
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

function eventLogDisplayKey(
  event: WorkspaceEvent,
  events: ReadonlyArray<WorkspaceEvent>,
): string {
  const payload = event.payload as Record<string, unknown>;
  return `${humaniseEventKind(event.kind, payload)}|${renderEventMessage(event.kind, payload, events)}`;
}

/** Drop back-to-back rows that would look identical in the activity log. */
function dedupeConsecutiveDisplayedEvents(
  events: ReadonlyArray<WorkspaceEvent>,
): WorkspaceEvent[] {
  const visible: WorkspaceEvent[] = [];
  let previousKey: string | null = null;

  for (const event of events) {
    const key = eventLogDisplayKey(event, events);
    if (key === previousKey) continue;
    previousKey = key;
    visible.push(event);
  }

  return visible;
}

function badgeVariant(
  kind: string,
  payload: Record<string, unknown>,
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (kind === "workflow_failed" || kind === "execution_failed") return "danger";
  if (kind === "workflow_completed" || kind === "validation_run") return "success";
  if (kind.startsWith("approval_")) return "warning";
  if (kind.startsWith("phase_") || kind.startsWith("budget_")) return "info";
  if (kind === "decision_input") {
    const subkind = payload.kind;
    if (subkind === "model_call_started" || subkind === "model_call_progress") {
      return "info";
    }
  }
  if (kind === "model_called") return "info";
  return "neutral";
}

interface ActivityBadgeProps {
  readonly label: string;
  readonly technicalDetail: string | null;
  readonly variant: "neutral" | "success" | "warning" | "danger" | "info";
}

function ActivityBadge({ label, technicalDetail, variant }: ActivityBadgeProps) {
  if (!technicalDetail) {
    return <Badge variant={variant}>{label}</Badge>;
  }

  return (
    <span className="group relative inline-flex max-w-full">
      <Badge
        variant={variant}
        className="cursor-help underline decoration-dotted decoration-slate-400/70 underline-offset-2"
        title={technicalDetail}
      >
        {label}
      </Badge>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-[calc(100%+0.35rem)] left-0 z-20 hidden max-w-xs rounded-md border border-slate-700/20 bg-slate-900 px-2.5 py-1.5 text-left text-[11px] font-normal leading-snug text-slate-100 shadow-lg group-hover:block group-focus-within:block"
      >
        {technicalDetail}
      </span>
    </span>
  );
}
