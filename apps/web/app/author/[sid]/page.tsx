"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { CancelWorkflowButton } from "@/components/cancel-workflow-button";
import { AuthorCustomBuildFailureCard, shouldShowAuthorCustomBuildFailureCard } from "@/components/author-custom-build-failure-card";
import { AuthorIntentMismatchCard, shouldShowAuthorIntentMismatchCard } from "@/components/author-intent-mismatch-card";
import { ApprovalPanel } from "@/components/approval-panel";
import { BudgetBanner, shouldShowBudgetBanner } from "@/components/budget-banner";
import { FinanceSummaryCard } from "@/components/finance-summary-card";
import { NextStepsCard } from "@/components/next-steps-card";
import { OutputPreviewCard } from "@/components/output-preview-card";
import { TechnicalDetailsDisclosure } from "@/components/technical-details-disclosure";
import { ValidationSummaryCard } from "@/components/validation-summary-card";
import { WorkflowStatusCard } from "@/components/workflow-status-card";
import { ErrorBanner } from "@/components/error-banner";
import { FailureCard } from "@/components/failure-card";
import { FileUpload } from "@/components/file-upload";
import {
  AnimatedSection,
  PageShell,
  SkeletonCard,
  StatusBadgeTransition,
} from "@/components/motion";
import { ResumeBanner } from "@/components/resume-banner";
import { UserQuestionPanel } from "@/components/user-question-panel";
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
  ApiError,
  finaliseSession,
  runSession,
  uploadFile,
  type FileUploadResponse,
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
  loadAuthorDescriptionDraft,
  saveAuthorDescriptionDraft,
} from "@/lib/session-draft-storage";
import {
  AUTHOR_SUCCESS_NEXT_STEPS,
  completedStageFailedDescription,
  isAuthorStoppedBeforeGenerationSession,
  renderSessionStatus,
} from "@/lib/ux-language";
import { resolveWorkflowDisplayTitle } from "@/lib/workflow-title";

export const dynamic = "force-dynamic";

interface PageProps {
  readonly params: { readonly sid: string };
}

const TEMPLATE_OPTIONS = [
  {
    name: "expense_exception_review",
    title: "Expense Exception Review",
    description:
      "Review an expense report, flag policy exceptions, and produce all rows plus a separate exceptions file.",
    sampleUrl: "/api/reference-samples/expense-exception-review",
    sampleFilename: "expense_exception_review.csv",
    defaultWorkflowPrompt:
      "Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.",
  },
  {
    name: "invoice_aging_cleanup",
    title: "Invoice Aging Cleanup",
    description:
      "Bundled invoice sample with ambiguous slash dates (for example 05/06/2026). The Author flow asks how to read those dates before model authoring, then builds an aging agent from your answer.",
    sampleUrl: "/api/reference-samples/invoice-aging",
    sampleFilename: "invoice_aging_cleanup_demo.csv",
    defaultWorkflowPrompt: `Build an agent that cleans up this invoice export for finance review.

Assign each open or overdue invoice to an aging bucket based on days between invoice_date and today (use 2026-05-25 as the reference date unless the file implies otherwise):
0-30, 31-60, 61-90, 90+.

Preserve every input row.

Add aging_bucket, days_outstanding, and review_required.

Flag high-risk overdue invoices where amount exceeds 2000 and status is overdue.

Produce outputs/output.csv and validation evidence.`,
  },
] as const;

const DEFAULT_TEMPLATE = TEMPLATE_OPTIONS[0].name;

/**
 * Author wizard (BP8 — step machine wired against the real backend).
 *
 * The wizard derives its current screen from the session row + the
 * event log; it never holds wizard state locally beyond the input
 * stage (reference hint + workflow description). Every gate is the
 * backend's call (INV-3): the wizard reads ``approval_requested``
 * events and renders :class:`ApprovalPanel` for the most recent
 * undecided one, but it does not "approve" anything client-side.
 */
export default function AuthorWizard({ params }: PageProps) {
  const sessionId = params.sid;
  const { session, events, error, refresh } = useSessionState(sessionId);
  const [runPending, setRunPending] = useState(false);

  const status = session?.status;
  const uploads = useMemo(() => uploadEventsToList(events), [events]);
  const pendingApproval = useMemo(() => findPendingApproval(events), [events]);
  const pendingQuestion = useMemo(() => findPendingQuestion(events), [events]);
  const workItemTitle = useMemo(
    () => (session ? deriveWorkItem("author", events) : "Finance workflow agent"),
    [session, events],
  );

  useEffect(() => {
    if (
      status === "running" ||
      isFailed(status) ||
      status === "completed" ||
      status?.startsWith("paused_")
    ) {
      setRunPending(false);
    }
  }, [status]);

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
              Upload sample files, describe what you need, and watch the workflow
              build a runnable agent with validated outputs.
            </p>
          </div>
          <div className="flex flex-col items-end gap-3">
            {isWorkflowCancellable(status, { runPending }) ? (
              <CancelWorkflowButton sessionId={sessionId} workflow="author" />
            ) : null}
            {status && status !== "running" ? (
              <StatusBadgeTransition statusKey={status}>
                {renderSessionStatus(status)}
              </StatusBadgeTransition>
            ) : null}
          </div>
        </header>

        {error ? <ErrorBanner error={error} /> : null}

        {session && !shouldHideAuthorResumeBanner(session) ? (
          <ResumeBanner session={session} sessionId={sessionId} />
        ) : null}

        {!session ? (
          <SkeletonCard lines={4} />
        ) : status === "created" ? (
          <div className={runPending ? "hidden" : undefined}>
            <InputStage
              sessionId={sessionId}
              uploads={uploads}
              onRunAccepted={() => setRunPending(true)}
              onRunFailed={() => setRunPending(false)}
              onRunComplete={refresh}
            />
          </div>
        ) : null}

        {session && (status === "running" || (runPending && status === "created")) ? (
          <RunningStage
            session={session}
            events={events}
            workItemTitle={workItemTitle}
          />
        ) : null}

        {session && pendingApproval && !runPending ? (
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

        {session && pendingQuestion && !shouldShowAuthorIntentMismatchCard(session) ? (
          <section className="mt-6">
            <UserQuestionPanel
              sessionId={sessionId}
              questionEventId={pendingQuestion.questionEventId}
              questionText={pendingQuestion.questionText}
              affectedColumns={pendingQuestion.affectedColumns}
              sampleValues={pendingQuestion.sampleValues}
              clarificationKind={pendingQuestion.clarificationKind}
              onAnswered={refresh}
            />
          </section>
        ) : null}

        {session && isFailed(status) && shouldShowAuthorIntentMismatchCard(session) ? (
          <section className="mt-6">
            <AuthorIntentMismatchCard
              sessionId={sessionId}
              session={session}
              events={events}
            />
          </section>
        ) : null}

        {session && isFailed(status) && shouldShowAuthorCustomBuildFailureCard(session) ? (
          <section className="mt-6">
            <AuthorCustomBuildFailureCard
              sessionId={sessionId}
              session={session}
              events={events}
            />
          </section>
        ) : null}

        {session &&
        isFailed(status) &&
        !shouldShowAuthorIntentMismatchCard(session) &&
        !shouldShowAuthorCustomBuildFailureCard(session) ? (
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
// Input stage — reference sample + uploads + description + Start
// ---------------------------------------------------------------------------

interface InputStageProps {
  readonly sessionId: string;
  readonly uploads: ReadonlyArray<UploadedRow>;
  readonly onRunAccepted: () => void;
  readonly onRunFailed: () => void;
  readonly onRunComplete: () => void;
}

function InputStage({
  sessionId,
  uploads,
  onRunAccepted,
  onRunFailed,
  onRunComplete,
}: InputStageProps) {
  const [templateHint, setTemplateHint] = useState<string>(DEFAULT_TEMPLATE);
  const [description, setDescription] = useState("");
  const [localUploads, setLocalUploads] = useState<UploadedRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const startingRef = useRef(false);

  useEffect(() => {
    setDescription(loadAuthorDescriptionDraft(sessionId));
  }, [sessionId]);

  useEffect(() => {
    saveAuthorDescriptionDraft(sessionId, description);
  }, [sessionId, description]);

  useEffect(() => {
    if (uploads.length === 0) return;
    setLocalUploads((prev) => mergeUploadedRows(prev, uploads));
  }, [uploads]);

  const selectedReference =
    TEMPLATE_OPTIONS.find((option) => option.name === templateHint) ?? null;
  const isCustomWorkflow = templateHint === "";
  const isReferenceSample = !isCustomWorkflow && selectedReference !== null;
  const trimmedDescription = description.trim();
  const hasUserDescription = trimmedDescription.length > 0;
  const effectiveWorkflowPrompt = isCustomWorkflow
    ? trimmedDescription
    : trimmedDescription || selectedReference?.defaultWorkflowPrompt || "";
  const allUploads = useMemo(
    () => mergeUploadedRows(localUploads, uploads),
    [localUploads, uploads],
  );
  const hasUploadedSample = allUploads.length > 0;
  const canStart =
    !busy &&
    (isReferenceSample
      ? effectiveWorkflowPrompt.length > 0
      : hasUserDescription && hasUploadedSample);
  const blockerText = startBlockerText({
    isCustomWorkflow,
    isReferenceSample,
    hasUploadedSample,
    hasUserDescription,
    hasWorkflowPrompt: effectiveWorkflowPrompt.length > 0,
  });

  function rememberUpload(response: FileUploadResponse) {
    const row = uploadedResponseToRow(response);
    setLocalUploads((prev) => mergeUploadedRows(prev, [row]));
  }

  async function onStart() {
    if (startingRef.current) return;
    if (!canStart) {
      if (blockerText) {
        setError(new Error(blockerText));
      }
      return;
    }
    startingRef.current = true;
    setBusy(true);
    setError(null);
    onRunAccepted();
    let uploadedRowsForPayload = allUploads;
    try {
      if (isReferenceSample && allUploads.length === 0 && selectedReference) {
        const uploaded = await uploadReferenceSample(sessionId, selectedReference);
        rememberUpload(uploaded);
        uploadedRowsForPayload = [uploadedResponseToRow(uploaded)];
      }
      await runSession(sessionId, {
        user_message: buildAuthorRunMessage({
          prompt: effectiveWorkflowPrompt,
          referenceTitle: isReferenceSample ? selectedReference?.title ?? null : null,
          templateHint: isCustomWorkflow ? undefined : templateHint,
          uploadedRows: uploadedRowsForPayload,
        }),
        template_hint: isCustomWorkflow ? undefined : templateHint,
      });
      onRunComplete();
    } catch (err) {
      onRunFailed();
      if (err instanceof ApiError && err.status === 409) {
        onRunComplete();
        return;
      }
      setError(err);
    } finally {
      startingRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Describe the finance workflow</CardTitle>
          <CardDescription>
            Describe what the agent should do in business terms. You
            do not need to write code. The model uses this description
            and the uploaded file profile to author the contract, code,
            and tests for the agent. Draft text is kept in this browser tab
            until you start the run.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            placeholder="Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file."
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Upload sample files</CardTitle>
          <CardDescription>
            Upload CSV or Excel examples for the workflow you described. These
            files are profiled for columns, sample rows, and workbook structure.
            Custom workflows require an upload; the reference sample can attach
            its bundled CSV automatically. Up to 25 MB per file.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <FileUpload
            sessionId={sessionId}
            onUploaded={(response) => {
              rememberUpload(response);
            }}
          />
          {allUploads.length > 0 ? (
            <ul className="mt-4 space-y-1 text-sm text-slate-700">
              {allUploads.map((u) => (
                <li key={u.relativePath} className="flex items-baseline gap-2">
                  <span className="font-medium text-slate-900">
                    {u.relativePath.split("/").pop()}
                  </span>
                  <span className="font-mono text-xs text-slate-500">
                    {u.relativePath}
                  </span>
                  {u.sizeBytes ? (
                    <span className="text-xs text-slate-500">
                      ({(u.sizeBytes / 1024).toFixed(1)} KB)
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-xs text-slate-500">
              No sample file uploaded yet.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Choose build context</CardTitle>
          <CardDescription>
            Use a bundled reference sample as context or build only from your
            upload. If anything is ambiguous, you will be asked straightforward
            questions before model authoring starts.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {TEMPLATE_OPTIONS.map((t) => (
              <label
                key={t.name}
                className="flex cursor-pointer items-start gap-3 rounded-md border border-emerald-200 bg-emerald-50/40 px-3 py-2 hover:bg-emerald-50"
              >
                <input
                  type="radio"
                  name="template"
                  value={t.name}
                  checked={templateHint === t.name}
                  onChange={() => setTemplateHint(t.name)}
                  className="mt-1"
                />
                <div>
                  <p className="text-xs font-medium text-slate-500">
                    Reference sample
                  </p>
                  <p className="mt-0.5 text-sm font-medium text-slate-900">
                    {t.title}
                  </p>
                  <p className="mt-1 text-xs text-slate-700">
                    {t.description}
                  </p>
                </div>
              </label>
            ))}
            <label className="flex cursor-pointer items-start gap-3 rounded-md border border-slate-200 px-3 py-2 opacity-80 hover:bg-slate-50">
              <input
                type="radio"
                name="template"
                value=""
                checked={templateHint === ""}
                onChange={() => setTemplateHint("")}
                className="mt-1"
              />
              <div>
                <p className="text-sm font-medium text-slate-900">
                  Custom finance workflow
                </p>
                <p className="mt-1 text-xs text-slate-600">
                  Build from your description and uploaded schema when no
                  reference sample fits. The system inspects your file, asks
                  clarifying questions when needed, asks the model to author
                  code and tests, runs checks, and only returns outputs when
                  validation evidence is available.
                </p>
              </div>
            </label>
          </div>
        </CardContent>
      </Card>

      {error ? <ErrorBanner error={error} /> : null}

      <Card>
        <CardHeader>
          <CardTitle>Start the workflow</CardTitle>
          <CardDescription>
            When you click Start, the system reads your file, builds the agent,
            runs checks, validates outputs, and prepares an audit archive.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-slate-500">
              {blockerText ?? "All inputs ready. Start when you are."}
            </p>
            <Button
              type="button"
              variant="primary"
              disabled={!canStart}
              onClick={onStart}
            >
              {busy ? "Starting…" : "Start agent →"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Running stage — progress chips derived from the event stream
// ---------------------------------------------------------------------------

interface RunningStageProps {
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly workItemTitle: string;
}

function RunningStage({
  session,
  events,
  workItemTitle,
}: RunningStageProps) {
  return (
    <section className="space-y-4">
      <WorkflowStatusCard
        session={session}
        events={events}
        workflow="author"
        workItemTitle={workItemTitle}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Completed stage — terminal banner + artifacts + finalise button
// ---------------------------------------------------------------------------

interface CompletedStageProps {
  readonly sessionId: string;
  readonly session: NonNullable<ReturnType<typeof useSessionState>["session"]>;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly onFinalised: () => void;
}

function CompletedStage({
  sessionId,
  session,
  events,
  onFinalised,
}: CompletedStageProps) {
  const failed = isFailed(session.status);
  const evidence = useMemo(() => collectCompletionEvidence(events), [events]);

  // Banner derives from artifact + validation evidence, not just
  // session.status (INV-1: evidence-based completion).
  const validationKnown = evidence.validation !== null;
  const validationPassed = evidence.validation?.overall_passed === true;
  const validationFailed = evidence.validation?.overall_passed === false;
  const hasModelAuthoredAgentAndTests =
    evidence.modelAuthoredFiles.includes("generated/agent.py") &&
    evidence.modelAuthoredFiles.includes("generated/tests/test_agent.py");
  const aiAuthoredEvidence =
    evidence.modelCallCount > 0 &&
    hasModelAuthoredAgentAndTests &&
    (evidence.completionVia === "ai_authored_workflow_build" ||
      evidence.hasModelAuthoringProvenance);
  const provenSuccess =
    !failed &&
    validationPassed &&
    evidence.hasValidationReport &&
    evidence.hasOutputCsv &&
    aiAuthoredEvidence;
  const incomplete =
    !failed &&
    (!validationKnown ||
      !validationPassed ||
      !evidence.hasValidationReport ||
      !evidence.hasOutputCsv ||
      !aiAuthoredEvidence);

  let variant: "success" | "warning" | "error" = "success";
  let title = "All done";
  let description =
    "The agent produced the expected output. Download the archive below, preview the validation report inline, or open the full audit log.";

  if (failed) {
    variant = "error";
    title = "Session stopped before finishing";
    description = completedStageFailedDescription(session.workflow, events);
  } else if (validationFailed) {
    variant = "error";
    title = "Validation failed";
    description = `The agent produced output but validation did not pass${
      evidence.failedLayers.length > 0
        ? ` (failed layers: ${evidence.failedLayers.join(", ")})`
        : ""
    }. Download the archive to inspect the validation report.`;
  } else if (incomplete) {
    variant = "warning";
    title = "Session ended without complete authoring evidence";
    description =
      "A successful Author run must include model-authored code, model-authored tests, validation evidence, and a validation report. The archive contains whatever was produced, but completion is not proven.";
  } else if (provenSuccess) {
    variant = "success";
    title = "AI-authored workflow validation passed";
    description =
      "The model-authored agent ran on your uploaded file, generated tests passed, contract-driven validation passed, and an audit trail was recorded. Download the output CSV or full archive below.";
  }

  return (
    <section className="space-y-4">
      {provenSuccess ? (
        <>
          <AnimatedSection index={0}>
            <FinanceSummaryCard sessionId={sessionId} events={events} />
          </AnimatedSection>

          <AnimatedSection index={1}>
            <NextStepsCard
              items={AUTHOR_SUCCESS_NEXT_STEPS.map((text) => ({ text }))}
            />
          </AnimatedSection>

          <AnimatedSection index={2}>
            <OutputPreviewCard sessionId={sessionId} events={events} />
          </AnimatedSection>
        </>
      ) : null}

      {!provenSuccess ? (
        <Alert variant={variant}>
          <AlertTitle>{title}</AlertTitle>
          <AlertDescription>{description}</AlertDescription>
        </Alert>
      ) : null}

      {!provenSuccess && evidence.hasValidationReport ? (
        <ValidationSummaryCard events={events} />
      ) : null}

      {!provenSuccess && evidence.hasOutputCsv ? (
        <OutputPreviewCard sessionId={sessionId} events={events} />
      ) : null}

      {!provenSuccess && evidence.workflowFailures.length > 0 ? (
        <Alert variant="error">
          <AlertTitle>
            workflow_failed events ({evidence.workflowFailures.length})
          </AlertTitle>
          <AlertDescription>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-xs">
              {evidence.workflowFailures.map((f, i) => (
                <li key={i}>
                  <code className="font-mono">{f.error_code ?? "unknown"}</code>
                  {f.stage ? ` (stage=${f.stage})` : ""}
                  {f.message ? `: ${f.message}` : ""}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      {!failed && session.status !== "completed" ? (
        <FinaliseButton sessionId={sessionId} onFinalised={onFinalised} />
      ) : null}
    </section>
  );
}

interface CompletionEvidence {
  hasValidationReport: boolean;
  hasOutputCsv: boolean;
  hasModelAuthoringProvenance: boolean;
  validation: { overall_passed: boolean } | null;
  failedLayers: string[];
  completionVia: string | null;
  modelCallCount: number;
  modelAuthoredFiles: string[];
  workflowFailures: Array<{ error_code?: string; message?: string; stage?: string }>;
}

function isValidationReportArtifact(
  artifactType: unknown,
  path: unknown,
): boolean {
  if (
    artifactType === "validation_report" ||
    artifactType === "system_validation_report" ||
    artifactType === "workflow_report"
  ) {
    return true;
  }
  return (
    typeof path === "string" && /(?:^|\/)validation_report\.md$/i.test(path)
  );
}

function isOutputCsvArtifact(artifactType: unknown, path: unknown): boolean {
  if (artifactType === "output_csv" || artifactType === "workflow_row_output") {
    return true;
  }
  return typeof path === "string" && /\boutput\.csv$/i.test(path);
}

function collectCompletionEvidence(
  events: ReadonlyArray<WorkspaceEvent>,
): CompletionEvidence {
  let hasValidationReport = false;
  let hasOutputCsv = false;
  let hasModelAuthoringProvenance = false;
  let validation: { overall_passed: boolean } | null = null;
  const failedLayers: string[] = [];
  let completionVia: string | null = null;
  let modelCallCount = 0;
  let modelAuthoredFiles: string[] = [];
  const workflowFailures: Array<{ error_code?: string; message?: string; stage?: string }> = [];
  for (const ev of events) {
    if (ev.kind === "artifact_generated") {
      const payload = ev.payload as Record<string, unknown>;
      const type = payload.artifact_type;
      const path = payload.path;
      if (isValidationReportArtifact(type, path)) hasValidationReport = true;
      if (isOutputCsvArtifact(type, path)) hasOutputCsv = true;
    }
    if (ev.kind === "validation_run") {
      const payload = ev.payload as Record<string, unknown>;
      const passed = payload.overall_passed;
      if (typeof passed === "boolean") {
        validation = { overall_passed: passed };
        const layers = payload.layer_results as
          | Array<Record<string, unknown>>
          | undefined;
        failedLayers.length = 0;
        if (layers) {
          for (const l of layers) {
            if (l.passed === false && l.skipped !== true && typeof l.layer === "string") {
              failedLayers.push(l.layer);
            }
          }
        }
      }
    }
    if (ev.kind === "model_called") {
      modelCallCount += 1;
    }
    if (ev.kind === "decision_input") {
      const payload = ev.payload as Record<string, unknown>;
      if (payload.kind === "model_authoring_provenance") {
        hasModelAuthoringProvenance = true;
        const files = payload.model_contributed_files;
        if (Array.isArray(files)) {
          modelAuthoredFiles = files.filter(
            (item): item is string => typeof item === "string",
          );
        }
      }
    }
    if (ev.kind === "workflow_failed") {
      const payload = ev.payload as Record<string, unknown>;
      workflowFailures.push({
        error_code: typeof payload.error_code === "string" ? payload.error_code : undefined,
        message: typeof payload.message === "string" ? payload.message : undefined,
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
      });
    }
    if (ev.kind === "workflow_completed") {
      const payload = ev.payload as Record<string, unknown>;
      const via = payload.via;
      if (typeof via === "string") {
        completionVia = via;
      }
    }
  }
  return {
    hasValidationReport,
    hasOutputCsv,
    hasModelAuthoringProvenance,
    validation,
    failedLayers,
    completionVia,
    modelCallCount,
    modelAuthoredFiles,
    workflowFailures,
  };
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

interface UploadedRow {
  readonly relativePath: string;
  readonly uploadedFileId: string | null;
  readonly sizeBytes: number | null;
}

type ReferenceSampleOption = (typeof TEMPLATE_OPTIONS)[number];

function mergeUploadedRows(
  primary: ReadonlyArray<UploadedRow>,
  secondary: ReadonlyArray<UploadedRow>,
): UploadedRow[] {
  const merged = new Map<string, UploadedRow>();
  for (const row of primary) merged.set(row.relativePath, row);
  for (const row of secondary) merged.set(row.relativePath, row);
  return Array.from(merged.values());
}

function startBlockerText({
  isCustomWorkflow,
  isReferenceSample,
  hasUploadedSample,
  hasUserDescription,
  hasWorkflowPrompt,
}: {
  readonly isCustomWorkflow: boolean;
  readonly isReferenceSample: boolean;
  readonly hasUploadedSample: boolean;
  readonly hasUserDescription: boolean;
  readonly hasWorkflowPrompt: boolean;
}): string | null {
  if (isReferenceSample) {
    if (!hasWorkflowPrompt) {
      return "Describe the finance workflow to continue.";
    }
    if (!hasUploadedSample) {
      return "Bundled reference sample will be attached automatically.";
    }
    return null;
  }
  if (isCustomWorkflow) {
    if (!hasUserDescription && !hasUploadedSample) {
      return "Describe the finance workflow and upload a CSV/XLSX sample to continue.";
    }
    if (!hasUserDescription) {
      return "Describe the finance workflow to continue.";
    }
    if (!hasUploadedSample) {
      return "Upload a CSV/XLSX sample to continue.";
    }
    return null;
  }
  if (!hasWorkflowPrompt) {
    return "Describe the finance workflow to continue.";
  }
  if (!hasUploadedSample) {
    return "Upload a CSV/XLSX sample to continue.";
  }
  return null;
}

async function uploadReferenceSample(
  sessionId: string,
  option: ReferenceSampleOption,
): Promise<FileUploadResponse> {
  const response = await fetch(option.sampleUrl, { cache: "no-store" });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Could not load bundled reference sample (${response.status})${
        detail ? `: ${detail}` : ""
      }`,
    );
  }
  const blob = await response.blob();
  const file = new File([blob], option.sampleFilename, {
    type: "text/csv",
  });
  return uploadFile(sessionId, file);
}

function uploadedResponseToRow(response: FileUploadResponse): UploadedRow {
  return {
    relativePath: response.relative_path,
    uploadedFileId: response.uploaded_file_id,
    sizeBytes: response.size_bytes,
  };
}

function buildAuthorRunMessage({
  prompt,
  referenceTitle,
  templateHint,
  uploadedRows,
}: {
  readonly prompt: string;
  readonly referenceTitle: string | null;
  readonly templateHint?: string;
  readonly uploadedRows: ReadonlyArray<UploadedRow>;
}): string {
  const resolved = resolveWorkflowDisplayTitle({
    templateName: templateHint || null,
    humanRequest: prompt,
  });
  const buildContext = resolved
    ? `Build context: ${resolved}`
    : referenceTitle
      ? `Build context: ${referenceTitle}`
      : "Build context: Finance workflow";
  const lines = [buildContext, "", "Workflow request:", prompt];
  if (uploadedRows.length > 0) {
    lines.push("", "Uploaded sample files:");
    for (const row of uploadedRows) {
      const id = row.uploadedFileId ? ` (uploaded_file_id=${row.uploadedFileId})` : "";
      lines.push(`- ${row.relativePath}${id}`);
    }
  }
  return lines.join("\n");
}

function uploadEventsToList(
  events: ReadonlyArray<WorkspaceEvent>,
): UploadedRow[] {
  const out: UploadedRow[] = [];
  const seen = new Set<string>();
  for (const ev of events) {
    if (ev.kind !== "file_uploaded") continue;
    const payload = ev.payload as Record<string, unknown>;
    const rel =
      (payload.relative_path as string | undefined) ??
      `uploads/${payload.filename as string | undefined}`;
    if (seen.has(rel)) continue;
    seen.add(rel);
    const uploadedFileId = payload.uploaded_file_id;
    out.push({
      relativePath: rel,
      uploadedFileId:
        typeof uploadedFileId === "string" ? uploadedFileId : null,
      sizeBytes:
        typeof payload.size_bytes === "number"
          ? (payload.size_bytes as number)
          : null,
    });
  }
  return out;
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
  readonly affectedColumns: ReadonlyArray<string>;
  readonly sampleValues: ReadonlyArray<{
    readonly column: string;
    readonly values: ReadonlyArray<string>;
  }>;
  readonly clarificationKind?: string;
}

function findPendingQuestion(
  events: ReadonlyArray<WorkspaceEvent>,
): PendingQuestion | null {
  // Track which questions have already been answered.
  const answeredIds = new Set<string>();
  for (const ev of events) {
    if (!ev || ev.kind !== "answer_received") continue;
    const qid = (ev.payload as Record<string, unknown>).question_event_id;
    if (typeof qid === "string") answeredIds.add(qid);
  }
  // Return the most recent unanswered question_asked event.
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (!ev || ev.kind !== "question_asked" || !ev.id) continue;
    if (answeredIds.has(ev.id)) continue;
    const payload = ev.payload as Record<string, unknown>;
    const text =
      (payload.question as string) ??
      (payload.plain_english_question as string) ??
      "(the agent asked a question)";
    const affectedRaw = payload.affected_columns;
    const affectedColumns = Array.isArray(affectedRaw)
      ? affectedRaw.filter((value): value is string => typeof value === "string")
      : [];
    const sampleRaw = payload.sample_values;
    const sampleValues: Array<{ column: string; values: string[] }> = [];
    if (sampleRaw && typeof sampleRaw === "object" && !Array.isArray(sampleRaw)) {
      for (const [column, values] of Object.entries(sampleRaw)) {
        if (!Array.isArray(values)) continue;
        const parsedValues = values.filter(
          (value): value is string => typeof value === "string",
        );
        if (parsedValues.length === 0) continue;
        sampleValues.push({ column, values: parsedValues });
      }
    }
    return {
      questionEventId: ev.id,
      questionText: text,
      affectedColumns,
      sampleValues,
      clarificationKind:
        typeof payload.clarification_kind === "string"
          ? payload.clarification_kind
          : undefined,
    };
  }
  return null;
}

function shouldHideAuthorResumeBanner(session: Session): boolean {
  return (
    isFailed(session.status) && isAuthorStoppedBeforeGenerationSession(session)
  );
}
