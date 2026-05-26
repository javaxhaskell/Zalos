"use client";

import { useEffect, useState } from "react";

import { ActivityPanel } from "@/components/activity-panel";
import { pollEvents, type WorkspaceEvent } from "@/lib/api-client";

interface EventLogStreamProps {
  readonly sessionId: string;
  readonly limit?: number;
}

/** Polls events and renders the milestone + collapsible full log UI. */
export function EventLogStream({ sessionId, limit = 200 }: EventLogStreamProps) {
  const [events, setEvents] = useState<WorkspaceEvent[]>([]);

  useEffect(() => {
    const stop = pollEvents(sessionId, (event) => {
      setEvents((prev) => {
        const next = [...prev, event];
        return next.length > limit ? next.slice(-limit) : next;
      });
    });
    return stop;
  }, [sessionId, limit]);

  return <ActivityPanel events={events} />;
}
