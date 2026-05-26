"use client";

import Link from "next/link";
import { useMemo } from "react";

import { CopyableSessionId } from "@/components/copyable-session-id";
import { SmoothCollapse } from "@/components/motion/smooth-collapse";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Session, WorkspaceEvent } from "@/lib/api-client";
import { archiveUrl, artifactUrl } from "@/lib/api-client";
import {
  AUTHOR_CUSTOM_BUILD_FAILURE_COPY,
  extractAuthorCustomBuildFailure,
  humaniseFailedCheck,
  humanisePytestSummary,
  isAuthorCustomBuildFailedSession,
  renderSessionStatus,
} from "@/lib/ux-language";
import { cn } from "@/lib/utils";

export interface AuthorCustomBuildFailureCardProps {
  readonly sessionId: string;
  readonly session: Session;
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

export function AuthorCustomBuildFailureCard({
  sessionId,
  session,
  events,
}: AuthorCustomBuildFailureCardProps) {
  const details = useMemo(
    () => extractAuthorCustomBuildFailure(events, session),
    [events, session],
  );
  const statusLabel = renderSessionStatus(session.status);
  const friendlyFailedCheck = humaniseFailedCheck(details.failedCheck);
  const friendlyPytestSummary = humanisePytestSummary(details.pytestSummary);
  const showRawFailedCheck =
    details.failedCheck &&
    friendlyFailedCheck &&
    friendlyFailedCheck !== details.failedCheck;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{AUTHOR_CUSTOM_BUILD_FAILURE_COPY.sectionTitle}</CardTitle>
        <p className="text-base font-medium text-slate-900">
          {AUTHOR_CUSTOM_BUILD_FAILURE_COPY.title}
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-slate-700">
        <dl className="space-y-3">
          <Row label="Status" value={statusLabel} />
          {details.failedLayer ? (
            <Row label="Check area" value={details.failedLayer} />
          ) : null}
          <Row
            label="Reason"
            value={details.reason ?? AUTHOR_CUSTOM_BUILD_FAILURE_COPY.reasonFallback}
          />
          {details.inspected ? (
            <Row label="What we inspected" value={details.inspected} />
          ) : null}
          {friendlyFailedCheck ? (
            <Row label="What failed" value={friendlyFailedCheck} />
          ) : null}
          {friendlyPytestSummary ? (
            <Row label="Automated checks" value={friendlyPytestSummary} />
          ) : null}
          {details.validationFailures.length ? (
            <Row
              label="Failed validation checks"
              value={details.validationFailures
                .slice(0, 3)
                .map((entry) => humaniseFailedCheck(entry) ?? entry)
                .join(" · ")}
            />
          ) : null}
          <Row
            label="Next step"
            value={details.nextSteps ?? AUTHOR_CUSTOM_BUILD_FAILURE_COPY.nextStepsFallback}
          />
        </dl>

        {details.diagnosticArtifacts.length ? (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Diagnostic artifacts
            </h3>
            <div className="mt-2 flex flex-wrap gap-2">
              {details.diagnosticArtifacts.map((artifact) => (
                <ArtifactLink
                  key={artifact.path}
                  sessionId={sessionId}
                  artifact={artifact}
                />
              ))}
              <a
                href={archiveUrl(sessionId)}
                download={`agentforge-session-${sessionId}.zip`}
                className={downloadClassName}
              >
                Download archive
              </a>
            </div>
          </div>
        ) : null}

        <SmoothCollapse
          title="Technical details"
          description="More detail on the error, checks, and this session."
          className="border-slate-200 bg-slate-50/80 shadow-none"
        >
          <dl className="space-y-1 text-xs text-slate-500">
            <div>
              <dt className="inline font-medium text-slate-600">Session ID: </dt>
              <dd className="inline">
                <CopyableSessionId sessionId={sessionId} inline />
              </dd>
            </div>
            {session.terminal_error_code ? (
              <div>
                <dt className="inline font-medium text-slate-600">Error code: </dt>
                <dd className="inline font-mono">{session.terminal_error_code}</dd>
              </div>
            ) : null}
            {details.failedLayer ? (
              <div>
                <dt className="inline font-medium text-slate-600">Failed layer: </dt>
                <dd className="inline font-mono">{details.failedLayer}</dd>
              </div>
            ) : null}
            {showRawFailedCheck ? (
              <div>
                <dt className="inline font-medium text-slate-600">Raw check output: </dt>
                <dd className="mt-1 font-mono text-xs text-slate-700 break-all whitespace-pre-wrap">
                  {details.failedCheck}
                </dd>
              </div>
            ) : null}
            {details.pytestSummary && friendlyPytestSummary !== details.pytestSummary ? (
              <div>
                <dt className="inline font-medium text-slate-600">Raw check summary: </dt>
                <dd className="mt-1 font-mono text-xs text-slate-700 break-all whitespace-pre-wrap">
                  {details.pytestSummary}
                </dd>
              </div>
            ) : null}
            {details.rawMessage ? (
              <div>
                <dt className="inline font-medium text-slate-600">Detailed log: </dt>
                <dd className="mt-1 font-mono text-xs text-slate-700 break-all whitespace-pre-wrap">
                  {details.rawMessage}
                </dd>
              </div>
            ) : null}
          </dl>
        </SmoothCollapse>

        <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-3 text-xs">
          <Link
            href={`/sessions/${sessionId}/audit`}
            className="font-medium text-slate-700 underline-offset-2 hover:underline"
          >
            Open full audit →
          </Link>
          <Link
            href="/"
            className="font-medium text-slate-700 underline-offset-2 hover:underline"
          >
            Start a new session
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export function shouldShowAuthorCustomBuildFailureCard(session: Session): boolean {
  return isAuthorCustomBuildFailedSession(session);
}

const downloadClassName = cn(
  "inline-flex h-8 items-center justify-center rounded-md border border-slate-300 px-3 text-xs font-medium",
  "bg-white text-slate-900 hover:bg-slate-50",
);

function ArtifactLink({
  sessionId,
  artifact,
}: {
  readonly sessionId: string;
  readonly artifact: { label: string; path: string };
}) {
  const isDirectory = artifact.path.endsWith("/") || !artifact.path.includes(".");
  if (isDirectory) {
    return (
      <Link
        href={`/sessions/${sessionId}/audit`}
        className={downloadClassName}
      >
        {artifact.label}
      </Link>
    );
  }
  return (
    <a
      href={artifactUrl(sessionId, artifact.path)}
      download={artifact.path.split("/").pop()}
      className={downloadClassName}
    >
      {artifact.label}
    </a>
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
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 text-slate-900">{value}</dd>
    </div>
  );
}
