"use client";

import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Session, WorkspaceEvent } from "@/lib/api-client";

/**
 * Per-session budget banner (BP11).
 *
 * Surfaces a token-usage bar so a finance user sees how much of the
 * per-session envelope the agent consumed and a operators can verify the
 * bounded-loop story (INV-12) at a glance.
 *
 * Live counters derive from the event log (MODEL_CALLED for tokens,
 * TOOL_INVOKED count for tool_calls, max event.step for steps). The
 * runner persists final totals to ``SessionRow`` after the flow
 * completes, but the wizard prefers event-derived numbers during
 * polling so the bars tick up in real time instead of jumping at
 * the end.
 *
 * Limits come from ``session.budget.*_limit`` which mirror
 * ``Settings.budget_*`` from the backend.
 */
export interface BudgetBannerProps {
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

/** Show token budget once a workflow has started (not on the input stage). */
export function shouldShowBudgetBanner(status: string | undefined): boolean {
  if (!status || status === "created") return false;
  return true;
}

function isActiveWorkflowStatus(status: string): boolean {
  return status === "running" || status.startsWith("paused_");
}

interface DerivedCounters {
  readonly tokens: number;
  readonly toolCalls: number;
  readonly steps: number;
  readonly wallSeconds: number;
}

export interface SessionBudgetSnapshot {
  readonly tokens: number;
  readonly toolCalls: number;
  readonly steps: number;
  readonly wallSeconds: number;
  readonly tokensLimit: number;
  readonly toolCallsLimit: number;
  readonly stepsLimit: number;
  readonly wallSecondsLimit: number;
  readonly modelCallCount: number;
  readonly modelStages: string[];
}

export function deriveSessionBudgetSnapshot(
  session: Session,
  events: ReadonlyArray<WorkspaceEvent>,
): SessionBudgetSnapshot {
  const derived = deriveCounters(events);
  const counters = chooseCounters(session, derived);
  const limits = session.budget;
  const modelStages: string[] = [];
  let modelCallCount = 0;
  for (const ev of events) {
    if (ev.kind !== "model_called") continue;
    modelCallCount += 1;
    const purpose = (ev.payload as Record<string, unknown>).purpose;
    if (typeof purpose === "string" && !modelStages.includes(purpose)) {
      modelStages.push(purpose);
    }
  }
  return {
    tokens: counters.tokens,
    toolCalls: counters.toolCalls,
    steps: counters.steps,
    wallSeconds: counters.wallSeconds,
    tokensLimit: limits?.tokens_limit ?? 150_000,
    toolCallsLimit: limits?.tool_calls_limit ?? 40,
    stepsLimit: limits?.steps_limit ?? 25,
    wallSecondsLimit: limits?.wall_seconds_limit ?? 1500,
    modelCallCount,
    modelStages,
  };
}

export function BudgetBanner({ session, events }: BudgetBannerProps) {
  const derived = useMemo(() => deriveCounters(events), [events]);
  const counters = chooseCounters(session, derived);
  const limits = session.budget;
  const live = isActiveWorkflowStatus(session.status);

  const bars = [
    {
      label: "Tokens",
      used: counters.tokens,
      limit: limits?.tokens_limit ?? 150_000,
    },
  ];

  const anyCritical = bars.some((b) => percent(b.used, b.limit) >= 1.0);
  const anyWarn = bars.some((b) => percent(b.used, b.limit) >= 0.75);
  const headlineTone: "neutral" | "warning" | "danger" = anyCritical
    ? "danger"
    : anyWarn
      ? "warning"
      : "neutral";

  const description = live
    ? anyCritical || anyWarn
      ? "Live token usage — approaching or at the per-session cap."
      : "Live token usage for this workflow."
    : anyCritical || anyWarn
      ? "Per-session token cap that bounds the agent loop."
      : "This session stayed within the configured token limit.";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Budget</span>
          <Badge variant={headlineTone}>
            {anyCritical
              ? "Limit reached"
              : anyWarn
                ? "Near limit"
                : live
                  ? "In progress"
                  : "Budget within limits"}
          </Badge>
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {bars.map((bar) => (
          <BudgetBar key={bar.label} {...bar} />
        ))}
      </CardContent>
    </Card>
  );
}

interface BudgetBarProps {
  readonly label: string;
  readonly used: number;
  readonly limit: number;
  readonly formatUsed?: (value: number) => string;
  readonly formatLimit?: (value: number) => string;
}

function BudgetBar({
  label,
  used,
  limit,
  formatUsed,
  formatLimit,
}: BudgetBarProps) {
  const pct = percent(used, limit);
  const tone = pct >= 1.0 ? "danger" : pct >= 0.75 ? "warning" : "ok";
  const widthPct = Math.min(pct * 100, 100);
  const usedLabel = formatUsed ? formatUsed(used) : used.toLocaleString();
  const limitLabel = formatLimit ? formatLimit(limit) : limit.toLocaleString();
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="font-medium text-slate-700">{label}</span>
        <span className="font-mono text-slate-600">
          {usedLabel} / {limitLabel}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-slate-200">
        <div
          className={
            tone === "danger"
              ? "h-full bg-red-500"
              : tone === "warning"
                ? "h-full bg-amber-500"
                : "h-full bg-emerald-500"
          }
          style={{ width: `${widthPct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function percent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return used / limit;
}

/**
 * Prefer event-derived counters when there are any events; fall back
 * to the persisted row when the wizard is showing a session that's
 * already completed (and the polling has finished).
 */
function chooseCounters(
  session: Session,
  derived: DerivedCounters,
): DerivedCounters {
  const budget = session.budget;
  const persisted = {
    tokens: budget?.tokens_used ?? 0,
    toolCalls: budget?.tool_calls_used ?? 0,
    steps: budget?.steps_used ?? 0,
    wallSeconds: budget?.wall_seconds_used ?? 0,
  };
  if (isActiveWorkflowStatus(session.status)) {
    // During a live run, merge event-derived totals with the session row
    // so the bar ticks up after each completed model call even when the
    // event tail hasn't arrived on this poll yet.
    return {
      tokens: Math.max(derived.tokens, persisted.tokens),
      toolCalls: Math.max(derived.toolCalls, persisted.toolCalls),
      steps: Math.max(derived.steps, persisted.steps),
      wallSeconds: Math.max(derived.wallSeconds, persisted.wallSeconds),
    };
  }
  if (derived.toolCalls > 0 || derived.steps > 0 || derived.tokens > 0) {
    return derived;
  }
  return persisted;
}

function deriveCounters(
  events: ReadonlyArray<WorkspaceEvent>,
): DerivedCounters {
  let tokens = 0;
  let toolCalls = 0;
  let maxStep = -1;
  let firstTs: string | null = null;
  let lastTs: string | null = null;
  for (const ev of events) {
    if (!ev) continue;
    if (firstTs === null) firstTs = ev.ts;
    lastTs = ev.ts;
    if (ev.kind === "model_called") {
      const usage = (ev.payload as Record<string, unknown>).usage as
        | Record<string, unknown>
        | undefined;
      if (usage) {
        const total = usage.total_tokens;
        if (typeof total === "number" && total > 0) {
          tokens += total;
        } else {
          // Fallback when total_tokens isn't pre-computed (older
          // events): sum input + output.
          const input = usage.input_tokens;
          const output = usage.output_tokens;
          if (typeof input === "number") tokens += input;
          if (typeof output === "number") tokens += output;
        }
      }
    }
    if (ev.kind === "tool_invoked") toolCalls += 1;
    if (typeof ev.step === "number" && ev.step > maxStep) maxStep = ev.step;
  }
  const steps = maxStep < 0 ? 0 : maxStep + 1;
  let wallSeconds = 0;
  if (firstTs && lastTs) {
    try {
      const a = new Date(firstTs).getTime();
      const b = new Date(lastTs).getTime();
      wallSeconds = Math.max(0, (b - a) / 1000);
    } catch {
      wallSeconds = 0;
    }
  }
  return { tokens, toolCalls, steps, wallSeconds };
}
