import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Diagnosis, Severity } from "@agentforge/shared-schemas";

/**
 * Render the diagnosis the model emits via ``diagnose``
 * (WORKFLOWS.md §2 step 8). Surfaces the suspected file + line range,
 * the plain-English root cause, and risk badges. The technical detail
 * (suspected_lines, confidence) is visible without a click — finance
 * users find "around line 34" reassuring; engineers find it actionable.
 */
export interface DiagnosisCardProps {
  readonly diagnosis: Diagnosis;
}

export function DiagnosisCard({ diagnosis }: DiagnosisCardProps) {
  const [lineStart, lineEnd] = diagnosis.suspected_lines;
  const lineLabel = lineStart === lineEnd ? `${lineStart}` : `${lineStart}-${lineEnd}`;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Diagnosis</CardTitle>
        <CardDescription>
          What the agent thinks is wrong, based on reading the code and
          reproducing the failure.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-slate-800">{diagnosis.root_cause}</p>
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            File
          </span>
          <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">
            {diagnosis.suspected_file}
          </code>
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Around line
          </span>
          <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">
            {lineLabel}
          </code>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Severity
          </span>
          <Badge variant={severityVariant(diagnosis.severity)}>
            {diagnosis.severity}
          </Badge>
          <span className="ml-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Fix risk
          </span>
          <Badge variant={severityVariant(diagnosis.fix_risk)}>
            {diagnosis.fix_risk}
          </Badge>
          <span className="ml-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Confidence
          </span>
          <span className="font-mono text-xs text-slate-700">
            {(diagnosis.confidence * 100).toFixed(0)}%
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function severityVariant(
  s: Severity,
): "success" | "info" | "warning" | "danger" | "neutral" {
  switch (s) {
    case "info":
      return "info";
    case "low":
      return "success";
    case "medium":
      return "warning";
    case "high":
    case "critical":
      return "danger";
    default:
      return "neutral";
  }
}
