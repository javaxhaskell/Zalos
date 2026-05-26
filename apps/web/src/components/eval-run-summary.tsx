import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { EvalRunSummary } from "@/lib/api-client";

/**
 * Aggregate card for the eval-run page. Surfaces the pass/fail
 * headline, when the run started + how long it took, and the per-tag
 * breakdown (author / repair / adversarial) so a operator can see at
 * a glance which dimensions are exercised.
 */
export interface EvalRunSummaryCardProps {
  readonly summary: EvalRunSummary;
}

export function EvalRunSummaryCard({ summary }: EvalRunSummaryCardProps) {
  const allPass = summary.failed === 0 && summary.total > 0;
  const wallSeconds = computeWallSeconds(summary);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Eval run</span>
          <Badge variant={allPass ? "success" : "danger"}>
            {summary.passed}/{summary.total} pass
          </Badge>
        </CardTitle>
        <CardDescription>
          Run id <code className="font-mono text-xs">{summary.id}</code>.
          Started {new Date(summary.started_at).toLocaleString()}
          {wallSeconds !== null ? ` · ${wallSeconds.toFixed(2)}s wall` : ""}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <SummaryStat
            label="Total scenarios"
            value={`${summary.total}`}
            variant="neutral"
          />
          <SummaryStat
            label="Passed"
            value={`${summary.passed}`}
            variant="success"
          />
          <SummaryStat
            label="Failed"
            value={`${summary.failed}`}
            variant={summary.failed === 0 ? "neutral" : "danger"}
          />
        </div>
        {summary.per_tag && Object.keys(summary.per_tag).length > 0 ? (
          <div className="mt-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Per kind
            </h3>
            <ul className="space-y-1 text-sm">
              {Object.entries(summary.per_tag).map(([tag, counts]) => {
                const passed = counts?.passed ?? 0;
                const failed = counts?.failed ?? 0;
                return (
                  <li key={tag} className="flex items-baseline gap-2">
                    <Badge variant="neutral">{tag}</Badge>
                    <span className="text-slate-700">
                      {passed} pass
                      {failed > 0 ? ` · ${failed} fail` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SummaryStat({
  label,
  value,
  variant,
}: {
  readonly label: string;
  readonly value: string;
  readonly variant: "neutral" | "success" | "danger";
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-lg font-semibold text-slate-900">{value}</span>
        <Badge variant={variant}>{label.toLowerCase()}</Badge>
      </div>
    </div>
  );
}

function computeWallSeconds(summary: EvalRunSummary): number | null {
  if (!summary.completed_at) return null;
  try {
    const started = new Date(summary.started_at).getTime();
    const completed = new Date(summary.completed_at).getTime();
    return (completed - started) / 1000;
  } catch {
    return null;
  }
}
