import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AgentSummary } from "@agentforge/shared-schemas";

// `AgentSummary` isn't on the OpenAPI route surface (no endpoint returns
// it directly); the type lives in the hand-written workflow.ts mirror.

/**
 * Render the agent summary the model emits via ``summarise_agent_purpose``
 * (WORKFLOWS.md §2 step 4). The summary surfaces in the repair wizard's
 * RunningStage so the user can confirm "yes, that's what this agent does"
 * before approving the patch.
 */
export interface AgentSummaryCardProps {
  readonly summary: AgentSummary;
}

export function AgentSummaryCard({ summary }: AgentSummaryCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>What this agent does</CardTitle>
        <CardDescription>
          Summary inferred from reading the agent code.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-slate-800">{summary.purpose}</p>
        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Inputs
            </dt>
            <dd className="mt-1 text-slate-700">
              {summary.inputs.length === 0 ? (
                <span className="text-slate-400">none recorded</span>
              ) : (
                <ul className="list-disc pl-5">
                  {summary.inputs.map((i) => (
                    <li key={i}>{i}</li>
                  ))}
                </ul>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Outputs
            </dt>
            <dd className="mt-1 text-slate-700">
              {summary.outputs.length === 0 ? (
                <span className="text-slate-400">none recorded</span>
              ) : (
                <ul className="list-disc pl-5">
                  {summary.outputs.map((o) => (
                    <li key={o}>{o}</li>
                  ))}
                </ul>
              )}
            </dd>
          </div>
        </dl>
        <div className="flex flex-wrap items-baseline gap-2 pt-1">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Entry point
          </span>
          <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">
            {summary.entry_point}
          </code>
          {summary.dependencies.length > 0 ? (
            <>
              <span className="ml-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Deps
              </span>
              {summary.dependencies.map((d) => (
                <Badge key={d} variant="neutral">
                  {d}
                </Badge>
              ))}
            </>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
