import { Badge } from "@/components/ui/badge";
import { TD, TR } from "@/components/ui/table";
import type { EvalRunResult } from "@/lib/api-client";

/**
 * One row in the eval-case table. Surfaces scenario id, pass/fail
 * badge, latency, and (when failed) the failure_reason in a
 * monospaced block so the operator can spot which terminal status
 * landed where expected status was COMPLETED.
 */
export interface EvalCaseRowProps {
  readonly result: EvalRunResult;
}

export function EvalCaseRow({ result }: EvalCaseRowProps) {
  return (
    <TR>
      <TD className="font-mono text-xs">{result.scenario_id}</TD>
      <TD className="w-20">
        <Badge variant={result.passed ? "success" : "danger"}>
          {result.passed ? "pass" : "fail"}
        </Badge>
      </TD>
      <TD className="w-32 font-mono text-xs text-slate-600">
        {formatLatency(result.latency_ms)}
      </TD>
      <TD>
        {result.failure_reason ? (
          <code className="block whitespace-pre-wrap font-mono text-xs text-red-700">
            {result.failure_reason}
          </code>
        ) : (
          <span className="text-xs text-slate-400">—</span>
        )}
      </TD>
    </TR>
  );
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}
