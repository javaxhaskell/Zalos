"use client";

import { useEffect, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { RunningProcessCard } from "@/components/workflow-status-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getEvents, listSessions, type SessionListItem, type WorkspaceEvent } from "@/lib/api-client";
import { deriveWorkItem } from "@/lib/session-work-item";

interface ActiveProcessRow extends SessionListItem {
  readonly workItem: string;
  readonly events: WorkspaceEvent[];
}

function isActiveProcess(status: string): boolean {
  return (
    status === "running" ||
    status.startsWith("paused_") ||
    status === "paused_approval" ||
    status === "paused_user"
  );
}

/**
 * Dashboard strip for workflows that are running or saved mid-flight.
 */
export function RunningProcessesPanel() {
  const [rows, setRows] = useState<ActiveProcessRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let alive = true;

    async function load() {
      try {
        const sessions = await listSessions(20);
        const active = sessions.filter((session) => isActiveProcess(session.status));
        const enriched = await Promise.all(
          active.map(async (session) => {
            let events: WorkspaceEvent[] = [];
            try {
              const response = await getEvents(session.id);
              events = response.events;
            } catch {
              events = [];
            }
            return {
              ...session,
              workItem: deriveWorkItem(session.workflow, events),
              events,
            };
          }),
        );
        if (alive) setRows(enriched);
      } catch (err) {
        if (alive) setError(err);
      }
    }

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 8000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  if (error) return <ErrorBanner error={error} />;
  if (rows === null || rows.length === 0) return null;

  return (
    <Card className="mb-8 border-emerald-100 bg-emerald-50/30">
      <CardHeader>
        <CardTitle>Active workflows</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {rows.map((row) => (
          <RunningProcessCard
            key={row.id}
            session={row}
            workItem={row.workItem}
          />
        ))}
      </CardContent>
    </Card>
  );
}
