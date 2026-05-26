"use client";

import Link from "next/link";

import { ArtifactDownloadPanel } from "@/components/artifact-download-panel";
import { AnimatedSection } from "@/components/motion";
import { CollapsibleSection } from "@/components/collapsible-section";
import type { WorkspaceEvent } from "@/lib/api-client";
import { shouldShowArtifactDownloads } from "@/lib/use-session-state";

export interface TechnicalAuditSectionProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly sessionStatus?: string;
}

/**
 * Secondary audit surface — collapsed by default on wizard pages.
 */
export function TechnicalAuditSection({
  sessionId,
  events,
  sessionStatus,
}: TechnicalAuditSectionProps) {
  const showArtifacts = shouldShowArtifactDownloads(sessionStatus);

  return (
    <AnimatedSection index={5}>
      <CollapsibleSection
      title="Technical audit details"
      description="Artifacts, activity log, and audit export."
    >
      <div className="space-y-4">
        {showArtifacts ? (
          <ArtifactDownloadPanel sessionId={sessionId} events={events} />
        ) : null}
        <p className="text-xs text-slate-600">
          Expand the activity timeline above for the full event log.
          Full audit export:{" "}
          <Link
            href={`/sessions/${sessionId}/audit`}
            className="font-medium text-slate-900 underline-offset-4 hover:underline"
          >
            Open audit view
          </Link>
          . Manifest and SESSION_README.md are included in the downloadable archive.
        </p>
      </div>
    </CollapsibleSection>
    </AnimatedSection>
  );
}
