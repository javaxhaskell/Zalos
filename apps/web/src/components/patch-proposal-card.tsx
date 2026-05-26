import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { PatchProposal } from "@agentforge/shared-schemas";

/**
 * Render the patch the model proposes via ``propose_patch``
 * (WORKFLOWS.md §2 step 9). The plain-English rationale sits primary;
 * the unified diff lives in a collapsed ``<details>`` block so the
 * finance-user view stays uncluttered (ADR-0006 — business summary
 * primary, technical change secondary).
 *
 * INV-10 — diff text is rendered with plain ``{...}``; no
 * ``dangerouslySetInnerHTML``.
 */
export interface PatchProposalCardProps {
  readonly proposal: PatchProposal;
}

export function PatchProposalCard({ proposal }: PatchProposalCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Proposed fix</CardTitle>
        <CardDescription>
          The agent proposes a change to <code>{proposal.file}</code>. Review
          the rationale, then approve or decline. The change is not applied
          until you approve.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-slate-800">{proposal.rationale}</p>
        <details className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            Show technical change
          </summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-800">
            {proposal.unified_diff}
          </pre>
        </details>
      </CardContent>
    </Card>
  );
}
