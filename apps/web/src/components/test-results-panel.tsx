import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import type { PerTestResult, TestResults } from "@agentforge/shared-schemas";

// `TestResults` isn't on the OpenAPI route surface yet (no endpoint
// returns it directly), so we import it from the hand-written mirror.
// Move to the generated types once BP8 exposes a run endpoint.

/**
 * Render :class:`TestResults` with humanised test names (the schema's
 * ``humanised_name`` field) instead of raw ``test_aging_april_invoices``.
 * Finance users see "April invoices in first bucket" rather than the
 * pytest identifier; the raw name is available in a column for debugging.
 */
export interface TestResultsPanelProps {
  readonly results: TestResults;
}

export function TestResultsPanel({ results }: TestResultsPanelProps) {
  const overall =
    results.failed_count === 0 && (results.error_count ?? 0) === 0
      ? "pass"
      : "fail";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Automated checks</span>
          <Badge variant={overall === "pass" ? "success" : "danger"}>
            {overall === "pass" ? "All pass" : "Failures"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-700">{results.summary_line}</p>
        {results.per_test && results.per_test.length > 0 ? (
          <Table>
            <THead>
              <TR>
                <TH>Check</TH>
                <TH className="w-24">Result</TH>
                <TH className="hidden md:table-cell">Identifier</TH>
              </TR>
            </THead>
            <TBody>
              {results.per_test.map((t: PerTestResult) => (
                <TR key={t.name}>
                  <TD>{t.humanised_name}</TD>
                  <TD>
                    <Badge variant={statusVariant(t.status)}>
                      {t.status}
                    </Badge>
                  </TD>
                  <TD className="hidden font-mono text-xs text-slate-500 md:table-cell">
                    {t.name}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}

function statusVariant(
  status: string,
): "success" | "danger" | "warning" | "neutral" {
  switch (status) {
    case "passed":
      return "success";
    case "failed":
    case "error":
      return "danger";
    case "skipped":
      return "warning";
    default:
      return "neutral";
  }
}
