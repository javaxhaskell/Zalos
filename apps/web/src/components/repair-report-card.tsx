"use client";

import { Badge } from "@/components/ui/badge";
import { CollapsibleSection } from "@/components/collapsible-section";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import type {
  FilesChangedEntry,
  RepairReport,
  TestRunSummary,
} from "@agentforge/shared-schemas";

export interface RepairReportCardProps {
  readonly report: RepairReport;
}

export function RepairReportCard({ report }: RepairReportCardProps) {
  const validationPass =
    report.validation_after.failed_count === 0 &&
    (report.golden_diff_zero === true || report.golden_diff_zero === null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Repair report preview</span>
          <Badge variant={validationPass ? "success" : "danger"}>
            {validationPass ? "Repair validated" : "Validation failed"}
          </Badge>
        </CardTitle>
        <CardDescription>
          Generated {new Date(report.generated_at).toLocaleString()}. Expand
          sections for full detail — the downloadable report matches this
          content.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <CollapsibleSection
          title="Problem reported"
          defaultOpen
          className="shadow-none"
        >
          <p className="text-slate-800">{report.problem}</p>
        </CollapsibleSection>

        <CollapsibleSection title="Failure reproduced" className="shadow-none">
          <p className="text-slate-800">{report.reproduction}</p>
        </CollapsibleSection>

        <CollapsibleSection title="Root cause" className="shadow-none">
          <p className="text-slate-800">{report.diagnosis}</p>
        </CollapsibleSection>

        <CollapsibleSection title="Fix applied" className="shadow-none">
          {report.files_changed.length === 0 ? (
            <p className="text-slate-500">No files changed.</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>File</TH>
                  <TH className="w-20">Hunks</TH>
                  <TH>Summary</TH>
                </TR>
              </THead>
              <TBody>
                {report.files_changed.map((f: FilesChangedEntry) => (
                  <TR key={f.file}>
                    <TD>
                      <code className="font-mono text-xs">{f.file}</code>
                    </TD>
                    <TD className="font-mono text-xs">{f.hunks_count}</TD>
                    <TD>{f.summary}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CollapsibleSection>

        <CollapsibleSection title="Validation evidence" className="shadow-none">
          <div className="grid gap-3 sm:grid-cols-2">
            <ValidationSummary
              label="Before fix"
              summary={report.validation_before}
            />
            <ValidationSummary
              label="After fix"
              summary={report.validation_after}
            />
          </div>
          {report.golden_diff_zero !== null ? (
            <p className="mt-2 text-xs text-slate-600">
              Golden-output diff:{" "}
              <Badge variant={report.golden_diff_zero ? "success" : "danger"}>
                {report.golden_diff_zero ? "zero drift" : "drift detected"}
              </Badge>
            </p>
          ) : null}
        </CollapsibleSection>

        <CollapsibleSection title="Remaining risks" className="shadow-none">
          <ReportList items={report.remaining_risks} emptyLabel="No remaining risks recorded." />
        </CollapsibleSection>

        <CollapsibleSection title="Next steps" className="shadow-none">
          <ReportList items={report.next_steps} emptyLabel="No next steps recorded." />
        </CollapsibleSection>
      </CardContent>
    </Card>
  );
}

function ReportList({
  items,
  emptyLabel,
}: {
  readonly items: readonly string[];
  readonly emptyLabel: string;
}) {
  if (items.length === 0) {
    return <p className="text-slate-500">{emptyLabel}</p>;
  }
  return (
    <ul className="list-disc space-y-1 pl-5 text-slate-800">
      {items.map((it) => (
        <li key={it}>{it}</li>
      ))}
    </ul>
  );
}

function ValidationSummary({
  label,
  summary,
}: {
  readonly label: string;
  readonly summary: TestRunSummary;
}) {
  const allPass = summary.failed_count === 0 && summary.total_count > 0;
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {label}
        </span>
        <Badge variant={allPass ? "success" : "danger"}>
          {summary.passed_count}/{summary.total_count} pass
        </Badge>
      </div>
      {summary.failing_tests.length > 0 ? (
        <ul className="mt-2 space-y-0.5 text-xs text-slate-600">
          {summary.failing_tests.map((t) => (
            <li key={t}>
              <code className="font-mono">{t}</code>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
