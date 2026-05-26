"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { CancelWorkflowButton } from "@/components/cancel-workflow-button";
import { AgentSummaryCard } from "@/components/agent-summary-card";
import { ApprovalPanel } from "@/components/approval-panel";
import { BudgetBanner, shouldShowBudgetBanner } from "@/components/budget-banner";
import { DiagnosisCard } from "@/components/diagnosis-card";
import { ErrorBanner } from "@/components/error-banner";
import { FailureCard } from "@/components/failure-card";
import {
  AnimatedSection,
  PageShell,
  SkeletonCard,
  StatusBadgeTransition,
} from "@/components/motion";
import { NextStepsCard } from "@/components/next-steps-card";
import { PatchProposalCard } from "@/components/patch-proposal-card";
import { RepairReportCard } from "@/components/repair-report-card";
import { RepairSummaryCard } from "@/components/repair-summary-card";
import { ResumeBanner } from "@/components/resume-banner";
import { TechnicalDetailsDisclosure } from "@/components/technical-details-disclosure";
import { WorkflowStatusCard } from "@/components/workflow-status-card";
import { UserQuestionPanel } from "@/components/user-question-panel";
import { ZipUpload } from "@/components/zip-upload";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  finaliseSession,
  loadFixture,
  runSession,
  type Session,
  type WorkspaceEvent,
} from "@/lib/api-client";
import {
  isFailed,
  isTerminal,
  isWorkflowCancellable,
  useSessionState,
} from "@/lib/use-session-state";
import { deriveWorkItem } from "@/lib/session-work-item";
import {
  loadRepairProblemDraft,
  saveRepairProblemDraft,
} from "@/lib/session-draft-storage";
import {
  REPAIR_SUCCESS_NEXT_STEPS,
  completedStageFailedDescription,
  isRepairCannotReproduceSession,
  renderSessionStatus,
} from "@/lib/ux-language";
import type {
  AgentSummary,
  Diagnosis,
  PatchProposal,
  RepairReport,
} from "@agentforge/shared-schemas";

export const dynamic = "force-dynamic";

interface PageProps {
  readonly params: { readonly sid: string };
}

const BUILTIN_SAMPLE_AGENT = {
  name: "invoice_aging_v2",
  title: "Invoice Aging Boundary Repair",
  description:
    "Built-in sample agent. An invoice-aging agent with a one-line boundary bug: invoices exactly 31 days overdue land in the wrong bucket. The repair flow reproduces the failure (2 of 7 tests fail), applies a minimal fix, and re-runs validation.",
} as const;

/**
 * Repair wizard (BP9 — step machine wired against the real backend).
 *
 * Mirror of the BP8 author wizard. The wizard derives its current
 * screen from ``session.status`` + the event log; it never holds
 * load-bearing state locally beyond the input stage. Every gate is
 * the backend's call (INV-3): the wizard reads ``approval_requested``
 * events and renders :class:`ApprovalPanel` for the most recent
 * undecided one. The four repair-specific cards (AgentSummaryCard,
 * DiagnosisCard, PatchProposalCard, RepairReportCard) render
 * whenever the corresponding event payload arrives.
 */
export default function RepairWizard({ params }: PageProps) {
  const sessionId = params.sid;
  const { session, events, error, refresh } = useSessionState(sessionId);

  const status = session?.status;
  const fixtureLoaded = useMemo(() => detectFixtureLoaded(events), [events]);
  const pendingApproval = useMemo(() => findPendingApproval(events), [events]);
  const pendingQuestion = useMemo(() => findPendingQuestion(events), [events]);
  const agentSummary = useMemo(() => extractAgentSummary(events, sessionId), [events, sessionId]);
  const diagnosis = useMemo(() => extractDiagnosis(events, sessionId), [events, sessionId]);
  const patchProposal = useMemo(() => extractPatchProposal(events), [events]);
  const repairReport = useMemo(() => extractRepairReport(events, sessionId), [events, sessionId]);
  const workItemTitle = useMemo(
    () => (session ? deriveWorkItem("repair", events) : "Agent repair"),
    [session, events],
  );

  return (
    <>
      <PageShell>
        <main className="mx-auto max-w-5xl px-6 py-10">
        <nav className="mb-4 text-sm text-slate-500">
          <Link href="/" className="hover:underline">
            ← Back to dashboard
          </Link>
        </nav>
        <header className="mb-6 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              {workItemTitle}
            </h1>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              We inspect the agent, reproduce the issue, apply a targeted fix,
              and produce a repair report you can download.
            </p>
          </div>
          <div className="flex flex-col items-end gap-3">
            {isWorkflowCancellable(status) ? (
              <CancelWorkflowButton sessionId={sessionId} workflow="repair" />
            ) : null}
            {status && status !== "running" ? (
              <StatusBadgeTransition statusKey={status}>
                {renderSessionStatus(status)}
              </StatusBadgeTransition>
            ) : null}
          </div>
        </header>

        {error ? <ErrorBanner error={error} /> : null}

        {!session ? (
          <SkeletonCard lines={4} />
        ) : null}

        {session && !shouldHideResumeBanner(session) ? (
          <ResumeBanner session={session} sessionId={sessionId} />
        ) : null}

        {session && status === "created" ? (
          <InputStage
            sessionId={sessionId}
            fixtureLoaded={fixtureLoaded}
            onChanged={refresh}
          />
        ) : null}

        {session && status === "running" ? (
          <RunningStage
            session={session}
            events={events}
            workItemTitle={workItemTitle}
            agentSummary={agentSummary}
            diagnosis={diagnosis}
            patchProposal={patchProposal}
          />
        ) : null}

        {session && pendingApproval ? (
          <section className="mt-6">
            <ApprovalPanel
              sessionId={sessionId}
              requestId={pendingApproval.requestId}
              toolName={pendingApproval.toolName}
              businessSummary={pendingApproval.summary}
              diff={pendingApproval.diff}
              onDecided={refresh}
            />
          </section>
        ) : null}

        {session && pendingQuestion ? (
          <section className="mt-6">
            <UserQuestionPanel
              sessionId={sessionId}
              questionEventId={pendingQuestion.questionEventId}
              questionText={pendingQuestion.questionText}
              onAnswered={refresh}
            />
          </section>
        ) : null}

        {session && isFailed(status) ? (
          <section className="mt-6">
            <FailureCard
              sessionId={sessionId}
              session={session}
              events={events}
            />
          </section>
        ) : null}

        {session && isTerminal(status) && !isFailed(status) ? (
          <CompletedStage
            sessionId={sessionId}
            session={session}
            events={events}
            report={repairReport}
            onFinalised={refresh}
          />
        ) : null}

        {session && shouldShowBudgetBanner(status) ? (
          <section className="mt-6">
            <BudgetBanner session={session} events={events} />
          </section>
        ) : null}

        {session && status !== "created" ? (
          <section className="mt-8">
            <TechnicalDetailsDisclosure
              sessionId={sessionId}
              events={events}
              sessionStatus={status}
            />
          </section>
        ) : null}
        </main>
      </PageShell>
    </>
  );
}

// ---------------------------------------------------------------------------
// Input stage — fixture picker + problem report + Start
// ---------------------------------------------------------------------------

interface InputStageProps {
  readonly sessionId: string;
  readonly fixtureLoaded: FixtureLoadInfo | null;
  readonly onChanged: () => void;
}

function InputStage({ sessionId, fixtureLoaded, onChanged }: InputStageProps) {
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState<"load" | "start" | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setProblem(loadRepairProblemDraft(sessionId));
  }, [sessionId]);

  useEffect(() => {
    saveRepairProblemDraft(sessionId, problem);
  }, [sessionId, problem]);

  const canStart = fixtureLoaded !== null && busy === null;

  async function onLoad() {
    setBusy("load");
    setError(null);
    try {
      await loadFixture(sessionId, BUILTIN_SAMPLE_AGENT.name);
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function onStart() {
    setBusy("start");
    setError(null);
    try {
      await runSession(sessionId, {
        user_message: problem.trim() || undefined,
      });
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Supply the agent to repair</CardTitle>
          <CardDescription>
            Upload your own agent ZIP, or load the built-in sample agent to
            try the validated repair path.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="rounded-lg border border-slate-300 bg-white p-4 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-700">
              Upload your own agent ZIP
            </p>
            <p className="mt-2 text-sm leading-relaxed text-slate-700">
              Upload an existing agent folder or repository as a ZIP, then
              describe the problem you want investigated.
            </p>
            <div className="mt-4">
              <ZipUpload
                sessionId={sessionId}
                onUploaded={() => onChanged()}
                disabled={fixtureLoaded !== null}
              />
            </div>
          </div>

          <div className="relative flex items-center gap-3 py-1">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs font-medium text-slate-500">Or</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-4">
            <p className="text-xs font-medium text-slate-500">
              Sample starting point
            </p>
            <p className="mt-1 text-sm font-medium text-slate-900">
              {BUILTIN_SAMPLE_AGENT.title}
            </p>
            <p className="mt-2 text-sm leading-relaxed text-slate-700">
              {BUILTIN_SAMPLE_AGENT.description}
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="secondary"
                disabled={busy !== null || fixtureLoaded !== null}
                onClick={onLoad}
              >
                {busy === "load"
                  ? "Loading…"
                  : fixtureLoaded !== null
                    ? "Sample agent loaded ✓"
                    : "Load sample agent"}
              </Button>
              {fixtureLoaded ? (
                <span className="text-xs text-slate-600">
                  {fixtureLoaded.fileCount} files staged in{" "}
                  <code className="font-mono">working/</code>
                </span>
              ) : null}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Describe the problem</CardTitle>
          <CardDescription>
            Optional. When left blank, the system uses the embedded{" "}
            <code className="font-mono text-xs">problem_report.md</code>{" "}
            from the loaded agent. When you type a problem here, that
            text takes precedence over the embedded report.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <textarea
            value={problem}
            onChange={(e) => setProblem(e.target.value)}
            rows={4}
            placeholder="e.g., Invoices that are exactly 31 days overdue are showing up in the 1-30 aging bucket instead of 31-60. INV-0005, INV-0013, and INV-0018 in particular."
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>What happens next</CardTitle>
          <CardDescription>
            When you click Start, the repair flow inspects the files,
            reads dependencies, reproduces the failure with pytest,
            identifies the root cause, proposes a targeted patch,
            applies it after approval, re-runs the tests, and produces a
            structured repair report with the problem, root cause, fix,
            validation evidence, and remaining risks.
          </CardDescription>
        </CardHeader>
      </Card>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="flex justify-end">
        <Button
          type="button"
          variant="primary"
          disabled={!canStart}
          onClick={onStart}
        >
          {busy === "start" ? "Starting…" : "Start agent →"}
        </Button>
      </div>
      {!canStart && busy === null ? (
        <p className="text-right text-xs text-slate-500">
          {fixtureLoaded === null
            ? "Upload an agent ZIP or load the sample agent to continue."
            : null}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Running stage — milestones + per-event cards as they arrive
// ---------------------------------------------------------------------------

interface RunningStageProps {
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly workItemTitle: string;
  readonly agentSummary: AgentSummary | null;
  readonly diagnosis: Diagnosis | null;
  readonly patchProposal: PatchProposal | null;
}

function RunningStage({
  session,
  events,
  workItemTitle,
  agentSummary,
  diagnosis,
  patchProposal,
}: RunningStageProps) {
  return (
    <section className="space-y-4">
      <WorkflowStatusCard
        session={session}
        events={events}
        workflow="repair"
        workItemTitle={workItemTitle}
      />
      {agentSummary ? <AgentSummaryCard summary={agentSummary} /> : null}
      {diagnosis ? <DiagnosisCard diagnosis={diagnosis} /> : null}
      {patchProposal ? <PatchProposalCard proposal={patchProposal} /> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Completed stage — RepairReportCard + finalise
// ---------------------------------------------------------------------------

interface CompletedStageProps {
  readonly sessionId: string;
  readonly session: NonNullable<ReturnType<typeof useSessionState>["session"]>;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly report: RepairReport | null;
  readonly onFinalised: () => void;
}

interface RepairEvidence {
  hasRepairReportMd: boolean;
  hasRepairReportJson: boolean;
  patchApplied: boolean;
  beforeFix: { passed: number; failed: number } | null;
  afterFix: { passed: number; failed: number } | null;
  changedFiles: string[];
  workflowFailures: Array<{ error_code?: string; stage?: string; message?: string }>;
}

function collectRepairEvidence(
  events: ReadonlyArray<WorkspaceEvent>,
): RepairEvidence {
  let hasRepairReportMd = false;
  let hasRepairReportJson = false;
  let patchApplied = false;
  let beforeFix: RepairEvidence["beforeFix"] = null;
  let afterFix: RepairEvidence["afterFix"] = null;
  const changedFiles: string[] = [];
  const workflowFailures: RepairEvidence["workflowFailures"] = [];
  for (const ev of events) {
    if (ev.kind === "artifact_generated") {
      const p = ev.payload as Record<string, unknown>;
      if (p.artifact_type === "repair_report") hasRepairReportMd = true;
      if (p.artifact_type === "repair_report_json") hasRepairReportJson = true;
    }
    if (ev.kind === "patch_applied") {
      patchApplied = true;
      const p = ev.payload as Record<string, unknown>;
      if (typeof p.file === "string" && !changedFiles.includes(p.file)) {
        changedFiles.push(p.file);
      }
    }
    if (ev.kind === "decision_input") {
      const p = ev.payload as Record<string, unknown>;
      if (p.kind === "pytest_before_fix") {
        beforeFix = {
          passed: typeof p.passed === "number" ? p.passed : 0,
          failed: typeof p.failed === "number" ? p.failed : 0,
        };
      }
      if (p.kind === "pytest_after_fix") {
        afterFix = {
          passed: typeof p.passed === "number" ? p.passed : 0,
          failed: typeof p.failed === "number" ? p.failed : 0,
        };
      }
    }
    if (ev.kind === "workflow_failed") {
      const p = ev.payload as Record<string, unknown>;
      workflowFailures.push({
        error_code: typeof p.error_code === "string" ? p.error_code : undefined,
        stage: typeof p.stage === "string" ? p.stage : undefined,
        message: typeof p.message === "string" ? p.message : undefined,
      });
    }
  }
  return {
    hasRepairReportMd,
    hasRepairReportJson,
    patchApplied,
    beforeFix,
    afterFix,
    changedFiles,
    workflowFailures,
  };
}

function CompletedStage({
  sessionId,
  session,
  events,
  report,
  onFinalised,
}: CompletedStageProps) {
  const failed = isFailed(session.status);
  const evidence = useMemo(() => collectRepairEvidence(events), [events]);
  const postFixPassed =
    evidence.afterFix !== null &&
    evidence.afterFix.failed === 0 &&
    evidence.afterFix.passed > 0;

  // Evidence gate: every checkbox in the take-home spec must hold.
  const gatesPassed =
    !failed &&
    evidence.hasRepairReportMd &&
    evidence.hasRepairReportJson &&
    evidence.patchApplied &&
    evidence.beforeFix !== null &&
    postFixPassed &&
    evidence.changedFiles.length > 0;

  let variant: "success" | "warning" | "error" = "success";
  let title = "Repair complete";
  let description =
    "Reproduced the failure, applied a targeted patch, re-ran the tests, and produced the repair report.";

  if (failed) {
    variant = "error";
    title = "Repair stopped before finishing";
    description = completedStageFailedDescription(session.workflow, events);
  } else if (!gatesPassed) {
    variant = "warning";
    title = !evidence.patchApplied
      ? "Repair incomplete. No patch applied."
      : !evidence.afterFix
        ? "Patch proposed but not validated"
        : !postFixPassed
          ? "Validation failed after patch"
          : !evidence.hasRepairReportMd
            ? "Repair incomplete. No repair report."
            : "Repair incomplete";
    description = (
      "Some evidence is missing. The system has not produced everything " +
      "required to call this a finished repair. Review the audit log and the " +
      "Repair-evidence panel below."
    );
  }

  return (
    <section className="space-y-4">
      {gatesPassed && !failed ? (
        <AnimatedSection index={0}>
          <RepairSummaryCard
            sessionId={sessionId}
            events={events}
            changedFiles={evidence.changedFiles}
            afterFix={evidence.afterFix}
            reportSummaries={report?.files_changed.map((entry) => entry.summary)}
          />
        </AnimatedSection>
      ) : (
        <AnimatedSection index={0}>
          <Alert variant={variant}>
            <AlertTitle>{title}</AlertTitle>
            <AlertDescription>{description}</AlertDescription>
          </Alert>
        </AnimatedSection>
      )}

      {gatesPassed && !failed ? null : (
        <AnimatedSection index={1}>
          <RepairEvidencePanel sessionId={sessionId} evidence={evidence} />
        </AnimatedSection>
      )}

      {gatesPassed && !failed ? (
        <AnimatedSection index={2}>
          <NextStepsCard
            items={REPAIR_SUCCESS_NEXT_STEPS.map((text) => ({ text }))}
          />
        </AnimatedSection>
      ) : null}

      {report ? (
        <AnimatedSection index={3}>
          <RepairReportCard report={report} />
        </AnimatedSection>
      ) : null}

      {!failed && session.status !== "completed" && gatesPassed ? (
        <FinaliseButton sessionId={sessionId} onFinalised={onFinalised} />
      ) : null}
    </section>
  );
}

function RepairEvidencePanel({
  evidence,
}: {
  readonly sessionId: string;
  readonly evidence: RepairEvidence;
}) {
  const checks: Array<{ label: string; ok: boolean; detail?: string }> = [
    {
      label: "Existing agent inspected",
      ok: evidence.changedFiles.length > 0 || evidence.patchApplied,
    },
    {
      label: "Pytest before fix",
      ok: evidence.beforeFix !== null,
      detail: evidence.beforeFix
        ? `${evidence.beforeFix.failed} failed, ${evidence.beforeFix.passed} passed`
        : "(not run)",
    },
    {
      label: "Failure reproduced (≥ 1 failing test)",
      ok: !!evidence.beforeFix && evidence.beforeFix.failed > 0,
    },
    { label: "Patch applied", ok: evidence.patchApplied },
    {
      label: "Changed file",
      ok: evidence.changedFiles.length > 0,
      detail: evidence.changedFiles.join(", "),
    },
    {
      label: "Pytest after fix",
      ok: evidence.afterFix !== null,
      detail: evidence.afterFix
        ? `${evidence.afterFix.failed} failed, ${evidence.afterFix.passed} passed`
        : "(not run)",
    },
    {
      label: "Post-fix tests all passed",
      ok:
        evidence.afterFix !== null &&
        evidence.afterFix.failed === 0 &&
        evidence.afterFix.passed > 0,
    },
    { label: "reports/repair_report.md", ok: evidence.hasRepairReportMd },
    { label: "reports/repair_report.json", ok: evidence.hasRepairReportJson },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Repair evidence</CardTitle>
        <CardDescription>
          Each gate must hold before the session can be marked complete.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1.5 text-sm">
        {checks.map((c) => (
          <li key={c.label} className="flex items-start gap-2">
            <span
              className={
                c.ok
                  ? "mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-100 text-xs font-bold text-emerald-700"
                  : "mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-amber-100 text-xs font-bold text-amber-700"
              }
            >
              {c.ok ? "✓" : "—"}
            </span>
            <span className="flex-1">
              <span className="text-slate-900">
                {c.detail ? `${c.label}: ${c.detail}` : c.label}
              </span>
            </span>
          </li>
        ))}
      </ul>
      {evidence.workflowFailures.length > 0 ? (
        <div className="mt-3 border-t border-slate-200 pt-2 text-xs text-slate-600">
          <p className="font-medium text-slate-800">
            workflow_failed events ({evidence.workflowFailures.length}):
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {evidence.workflowFailures.map((f, i) => (
              <li key={i}>
                <code className="font-mono">{f.error_code ?? "unknown"}</code>
                {f.stage ? ` (stage=${f.stage})` : ""}
                {f.message ? `: ${f.message}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      </CardContent>
    </Card>
  );
}

interface FinaliseButtonProps {
  readonly sessionId: string;
  readonly onFinalised: () => void;
}

function FinaliseButton({ sessionId, onFinalised }: FinaliseButtonProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function onClick() {
    setBusy(true);
    setError(null);
    try {
      await finaliseSession(sessionId);
      onFinalised();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2">
      {error ? <ErrorBanner error={error} /> : null}
      <Button
        type="button"
        variant="primary"
        disabled={busy}
        onClick={onClick}
      >
        {busy ? "Finalising…" : "Finalise session"}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Event-derived helpers
// ---------------------------------------------------------------------------

function shouldHideResumeBanner(session: Session): boolean {
  return isFailed(session.status) && isRepairCannotReproduceSession(session);
}

interface FixtureLoadInfo {
  readonly fixtureName: string;
  readonly fileCount: number;
  readonly stagedGolden: string | null;
}

function detectFixtureLoaded(
  events: ReadonlyArray<WorkspaceEvent>,
): FixtureLoadInfo | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "decision_input") continue;
    const payload = ev.payload as Record<string, unknown>;
    if (payload.kind === "fixture_loaded") {
      return {
        fixtureName: (payload.fixture_name as string) ?? "unknown",
        fileCount: (payload.file_count as number) ?? 0,
        stagedGolden: (payload.staged_golden_path as string | null) ?? null,
      };
    }
    if (payload.kind === "agent_zip_uploaded") {
      return {
        fixtureName: (payload.archive_filename as string) ?? "uploaded.zip",
        fileCount: (payload.file_count as number) ?? 0,
        stagedGolden: (payload.staged_golden_path as string | null) ?? null,
      };
    }
  }
  return null;
}

interface PendingApproval {
  readonly requestId: string;
  readonly toolName: string;
  readonly summary: string;
  readonly diff: string | null;
}

function findPendingApproval(
  events: ReadonlyArray<WorkspaceEvent>,
): PendingApproval | null {
  const decided = new Set<string>();
  for (const ev of events) {
    if (ev.kind === "approval_granted" || ev.kind === "approval_declined") {
      const rid = (ev.payload as Record<string, unknown>).request_id;
      if (typeof rid === "string") decided.add(rid);
    }
  }
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "approval_requested") continue;
    const payload = ev.payload as Record<string, unknown>;
    const rid = payload.request_id;
    if (typeof rid !== "string" || decided.has(rid)) continue;
    return {
      requestId: rid,
      toolName: (payload.tool_name as string) ?? "unknown",
      summary:
        (payload.business_summary as string) ??
        `The agent wants to run ${(payload.tool_name as string) ?? "an action"}.`,
      diff: (payload.diff as string | null) ?? null,
    };
  }
  return null;
}

interface PendingQuestion {
  readonly questionEventId: string;
  readonly questionText: string;
}

function findPendingQuestion(
  events: ReadonlyArray<WorkspaceEvent>,
): PendingQuestion | null {
  const answeredIds = new Set<string>();
  for (const ev of events) {
    if (!ev || ev.kind !== "answer_received") continue;
    const qid = (ev.payload as Record<string, unknown>).question_event_id;
    if (typeof qid === "string") answeredIds.add(qid);
  }
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "question_asked" || !ev.id) continue;
    if (answeredIds.has(ev.id)) continue;
    const payload = ev.payload as Record<string, unknown>;
    const text =
      (payload.question as string) ??
      (payload.plain_english_question as string) ??
      "(the agent asked a question)";
    return { questionEventId: ev.id, questionText: text };
  }
  return null;
}

function extractAgentSummary(
  events: ReadonlyArray<WorkspaceEvent>,
  sessionId: string,
): AgentSummary | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "agent_summary_produced") continue;
    const p = ev.payload as Record<string, unknown>;
    return {
      session_id: (p.session_id as string) ?? sessionId,
      purpose: (p.purpose as string) ?? "",
      inputs: (p.inputs as string[]) ?? [],
      outputs: (p.outputs as string[]) ?? [],
      entry_point: (p.entry_point as string) ?? "",
      dependencies: (p.dependencies as string[]) ?? [],
    };
  }
  return null;
}

function extractDiagnosis(
  events: ReadonlyArray<WorkspaceEvent>,
  sessionId: string,
): Diagnosis | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "diagnosis_produced") continue;
    const p = ev.payload as Record<string, unknown>;
    const lines = (p.suspected_lines as [number, number]) ?? [0, 0];
    return {
      id: (p.id as string) ?? "",
      session_id: (p.session_id as string) ?? sessionId,
      suspected_file: (p.suspected_file as string) ?? "",
      suspected_lines: lines,
      root_cause: (p.root_cause as string) ?? "",
      severity:
        (p.severity as Diagnosis["severity"]) ?? ("info" as Diagnosis["severity"]),
      fix_risk:
        (p.fix_risk as Diagnosis["fix_risk"]) ?? ("info" as Diagnosis["fix_risk"]),
      confidence: (p.confidence as number) ?? 0,
    };
  }
  return null;
}

function extractPatchProposal(
  events: ReadonlyArray<WorkspaceEvent>,
): PatchProposal | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "patch_proposed") continue;
    const p = ev.payload as Record<string, unknown>;
    return {
      id: (p.id as string) ?? "",
      diagnosis_id: (p.diagnosis_id as string) ?? "",
      file: (p.file as string) ?? "",
      unified_diff: (p.unified_diff as string) ?? "",
      rationale: (p.rationale as string) ?? "",
    };
  }
  return null;
}

function extractRepairReport(
  events: ReadonlyArray<WorkspaceEvent>,
  sessionId: string,
): RepairReport | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "repair_report_generated") continue;
    const p = ev.payload as Record<string, unknown>;
    const report = p.report as Record<string, unknown> | undefined;
    if (!report) continue;
    return {
      session_id: (report.session_id as string) ?? sessionId,
      generated_at: (report.generated_at as string) ?? new Date().toISOString(),
      problem: (report.problem as string) ?? "",
      reproduction: (report.reproduction as string) ?? "",
      diagnosis: (report.diagnosis as string) ?? "",
      files_changed: (report.files_changed as RepairReport["files_changed"]) ?? [],
      validation_before:
        (report.validation_before as RepairReport["validation_before"]) ?? {
          passed_count: 0,
          failed_count: 0,
          total_count: 0,
          failing_tests: [],
        },
      validation_after:
        (report.validation_after as RepairReport["validation_after"]) ?? {
          passed_count: 0,
          failed_count: 0,
          total_count: 0,
          failing_tests: [],
        },
      golden_diff_zero: (report.golden_diff_zero as boolean | null) ?? null,
      remaining_risks: (report.remaining_risks as string[]) ?? [],
      next_steps: (report.next_steps as string[]) ?? [],
    };
  }
  return null;
}
