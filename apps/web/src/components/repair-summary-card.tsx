"use client";

import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SmoothCollapse } from "@/components/motion/smooth-collapse";
import {
  archiveUrl,
  artifactUrl,
  type WorkspaceEvent,
} from "@/lib/api-client";
import { deriveWorkItem } from "@/lib/session-work-item";
import {
  REPAIR_SUCCESS_COPY,
  humaniseRepairProblemText,
  humaniseRepairRootCause,
  humaniseRepairWorkItem,
  repairChangeScopeLabel,
  repairQualityLabel,
  repairUpdatedFileLabel,
} from "@/lib/ux-language";
import { cn } from "@/lib/utils";

export interface RepairSummaryCardProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly changedFiles: readonly string[];
  readonly afterFix: { failed: number; passed: number } | null;
  readonly reportSummaries?: readonly string[];
}

export function RepairSummaryCard({
  sessionId,
  events,
  changedFiles,
  afterFix,
  reportSummaries = [],
}: RepairSummaryCardProps) {
  const meta = useMemo(() => deriveRepairMeta(events), [events]);
  const changeScope = useMemo(
    () =>
      repairChangeScopeLabel(events, {
        fixtureName: meta.fixtureName,
        reportSummaries,
      }),
    [events, meta.fixtureName, reportSummaries],
  );

  return (
    <Card className="border-emerald-200 bg-emerald-50/40">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 text-lg">
          <span>Your repair is ready</span>
          <Badge variant="success">{REPAIR_SUCCESS_COPY.badgeReady}</Badge>
        </CardTitle>
        <CardDescription>{REPAIR_SUCCESS_COPY.cardDescription}</CardDescription>
      </CardHeader>
      <CardContent className="pb-5">
        <div className="space-y-4 text-sm">
          <InlineRow
            label={REPAIR_SUCCESS_COPY.labelWorkItem}
            value={meta.workItem}
          />
          <IssueSummary problem={meta.problem} rootCause={meta.rootCause} />
          <InlineRow
            label={REPAIR_SUCCESS_COPY.labelFixVerified}
            value={REPAIR_SUCCESS_COPY.fixVerifiedValue}
            valueClassName="font-medium text-emerald-800"
          />
        </div>
      </CardContent>
      <CardFooter className="flex-col items-stretch gap-5 border-emerald-200/80 bg-transparent px-4 pb-4 pt-4">
        <div className="flex w-full flex-wrap items-center gap-3">
          <a
            href={artifactUrl(sessionId, "reports/repair_report.md")}
            download="repair_report.md"
            className={buttonPrimary}
          >
            {REPAIR_SUCCESS_COPY.downloadRepairReport}
          </a>
          <a
            href={archiveUrl(sessionId)}
            download={`agentforge-session-${sessionId}.zip`}
            className={buttonSecondary}
          >
            {REPAIR_SUCCESS_COPY.downloadFullPackage}
          </a>
        </div>

        <SmoothCollapse
          title={REPAIR_SUCCESS_COPY.fixDetailsTitle}
          description={REPAIR_SUCCESS_COPY.fixDetailsDescription}
          className="w-full border-slate-200 bg-white/80 shadow-none"
        >
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <Row
              label={REPAIR_SUCCESS_COPY.labelQualityCheck}
              value={repairQualityLabel(afterFix)}
            />
            <Row
              label={REPAIR_SUCCESS_COPY.labelUpdatedFile}
              value={repairUpdatedFileLabel(changedFiles)}
            />
            <Row
              label={REPAIR_SUCCESS_COPY.labelRepairReport}
              value={REPAIR_SUCCESS_COPY.repairReportReady}
            />
            <Row
              label={REPAIR_SUCCESS_COPY.labelChangeScope}
              value={changeScope}
            />
          </dl>
        </SmoothCollapse>
      </CardFooter>
    </Card>
  );
}

function InlineRow({
  label,
  value,
  valueClassName,
}: {
  readonly label: string;
  readonly value: string;
  readonly valueClassName?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 py-1">
      <span className="text-xs font-medium text-slate-500">{label}</span>
      <span className={cn("text-slate-900", valueClassName)}>{value}</span>
    </div>
  );
}

function IssueSummary({
  problem,
  rootCause,
}: {
  readonly problem: string;
  readonly rootCause: string;
}) {
  const showCause = rootCause.trim() && rootCause !== "—";
  return (
    <div
      className="rounded-lg border border-emerald-100/80 bg-white/70 px-4 py-3.5"
      aria-label={`${REPAIR_SUCCESS_COPY.labelReportedProblem}. ${REPAIR_SUCCESS_COPY.labelRootCause}.`}
    >
      <p className="font-medium leading-relaxed text-slate-900">{problem}</p>
      {showCause ? (
        <p className="mt-1.5 text-xs leading-relaxed text-slate-600">{rootCause}</p>
      ) : null}
    </div>
  );
}

function Row({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-0.5 text-slate-900">{value}</dd>
    </div>
  );
}

function deriveRepairMeta(events: ReadonlyArray<WorkspaceEvent>): {
  workItem: string;
  problem: string;
  rootCause: string;
  rawProblem: string;
  rawRootCause: string;
  fixtureName: string | null;
} {
  let fixtureName: string | null = null;
  let rawProblem = "";
  let rawRootCause = "";

  for (const ev of events) {
    if (ev.kind === "decision_input") {
      const p = ev.payload as Record<string, unknown>;
      if (p.kind === "fixture_loaded" && typeof p.fixture_name === "string") {
        fixtureName = p.fixture_name;
      }
      if (p.kind === "primary_problem_resolved") {
        if (typeof p.text === "string" && p.text.trim()) {
          rawProblem = p.text.trim();
        }
      }
      if (p.kind === "repair_proposal" && typeof p.root_cause === "string") {
        rawRootCause = p.root_cause.trim();
      }
    }
    if (ev.kind === "diagnosis_produced") {
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.root_cause === "string" && p.root_cause.trim()) {
        rawRootCause = p.root_cause.trim();
      }
    }
  }

  const workItem = humaniseRepairWorkItem({
    fixtureName,
    fallback: deriveWorkItem("repair", events),
    humanRequest: rawProblem || undefined,
  });
  const problem = humaniseRepairProblemText(rawProblem, { fixtureName });
  const rootCause = humaniseRepairRootCause(rawRootCause, { fixtureName });

  return {
    workItem,
    problem,
    rootCause,
    rawProblem,
    rawRootCause,
    fixtureName,
  };
}

const buttonPrimary = cn(
  "inline-flex h-10 items-center justify-center rounded-md px-4 text-sm font-medium",
  "bg-slate-900 text-white hover:bg-slate-800",
);

const buttonSecondary = cn(
  "inline-flex h-10 items-center justify-center rounded-md border border-slate-300 px-4 text-sm font-medium",
  "bg-white text-slate-900 hover:bg-slate-50",
);
