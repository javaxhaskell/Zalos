"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/error-banner";
import { cancelSession, createSession, type Workflow } from "@/lib/api-client";

export interface CancelWorkflowButtonProps {
  readonly sessionId: string;
  readonly workflow: Workflow;
}

/**
 * Cancels the current workflow and starts a fresh session on the
 * describe-your-workflow input stage.
 */
export function CancelWorkflowButton({
  sessionId,
  workflow,
}: CancelWorkflowButtonProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function onCancel() {
    setBusy(true);
    setError(null);
    try {
      await cancelSession(sessionId);
      const fresh = await createSession(workflow);
      router.push(`/${workflow}/${fresh.id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      {error ? <ErrorBanner error={error} /> : null}
      <Button
      type="button"
      variant="secondary"
      size="sm"
      disabled={busy}
      onClick={() => void onCancel()}
    >
      {busy ? "Cancelling…" : "Cancel workflow"}
    </Button>
    </div>
  );
}
