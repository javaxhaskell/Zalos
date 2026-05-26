"use client";

import { CollapsibleSection } from "@/components/collapsible-section";
import { EventLogTable } from "@/components/event-log-table";
import { ArtifactDownloadPanel } from "@/components/artifact-download-panel";
import type { WorkspaceEvent } from "@/lib/api-client";
import { TECHNICAL_DETAILS_PAGE_SECTION_ID } from "@/lib/technical-details";
import { shouldShowArtifactDownloads } from "@/lib/use-session-state";

export interface TechnicalDetailsDisclosureProps {
  readonly sessionId: string;
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly sessionStatus?: string;
}

/**
 * Collapsed technical evidence — downloads and event log.
 */
export function TechnicalDetailsDisclosure({
  sessionId,
  events,
  sessionStatus,
}: TechnicalDetailsDisclosureProps) {
  const showArtifacts = shouldShowArtifactDownloads(sessionStatus);

  return (
    <div id={TECHNICAL_DETAILS_PAGE_SECTION_ID}>
      <CollapsibleSection
        title="Technical details"
        description="Event log, downloads, and audit trail."
        defaultOpen={false}
        sectionId={TECHNICAL_DETAILS_PAGE_SECTION_ID}
      >
      <div className="space-y-4">
        {showArtifacts ? (
          <ArtifactDownloadPanel sessionId={sessionId} events={events} />
        ) : null}
        <CollapsibleSection
          title="Full event log"
          description="Step-by-step record of what happened in this session."
        >
          <EventLogTable events={events} />
        </CollapsibleSection>
      </div>
    </CollapsibleSection>
    </div>
  );
}
