"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { AnimatedSection } from "@/components/motion";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { createSession, type Workflow } from "@/lib/api-client";

interface WorkflowCardProps {
  readonly title: string;
  readonly description: string;
  readonly sampleTitle: string;
  readonly sampleHelper?: string;
  readonly workflow: Workflow;
  readonly buttonLabel: string;
  readonly busyLabel: string;
  readonly busy: Workflow | null;
  readonly onStart: (workflow: Workflow) => void;
}

function WorkflowCard({
  title,
  description,
  sampleTitle,
  sampleHelper,
  workflow,
  buttonLabel,
  busyLabel,
  busy,
  onStart,
}: WorkflowCardProps) {
  const isBusy = busy === workflow;

  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription className="min-h-[4.5rem] leading-relaxed">
          {description}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 text-sm">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Reference sample
        </p>
        <div className="rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2.5">
          <p className="font-medium text-slate-900">{sampleTitle}</p>
        </div>
        {sampleHelper ? (
          <p className="text-xs leading-relaxed text-slate-600">{sampleHelper}</p>
        ) : null}
      </CardContent>
      <CardFooter className="mt-auto">
        <Button
          type="button"
          variant="primary"
          disabled={busy !== null}
          onClick={() => onStart(workflow)}
        >
          {isBusy ? busyLabel : buttonLabel}
        </Button>
      </CardFooter>
    </Card>
  );
}

/**
 * Dashboard workflow cards — Author and Repair entry points.
 */
export function WorkflowPicker() {
  const router = useRouter();
  const [busy, setBusy] = useState<Workflow | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function start(workflow: Workflow) {
    setBusy(workflow);
    setError(null);
    try {
      const session = await createSession(workflow);
      router.push(`/${workflow}/${session.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 sm:items-stretch">
        <AnimatedSection index={0}>
          <WorkflowCard
            title="Author a new agent"
            description="Describe a finance workflow, upload CSV/XLSX sample files, and generate a runnable Python agent with output files, validation evidence, and an audit archive."
            sampleTitle="Expense Exception Review"
            workflow="author"
            buttonLabel="Start authoring"
            busyLabel="Starting…"
            busy={busy}
            onStart={(w) => void start(w)}
          />
        </AnimatedSection>
        <AnimatedSection index={1}>
          <WorkflowCard
            title="Repair an existing agent"
            description="Upload an existing agent ZIP or use a sample agent, describe the issue, and let the system inspect files, reproduce the failure, apply a targeted fix where safe, rerun checks, and produce a repair report."
            sampleTitle="Invoice Aging Boundary Repair"
            workflow="repair"
            buttonLabel="Start repair"
            busyLabel="Starting…"
            busy={busy}
            onStart={(w) => void start(w)}
          />
        </AnimatedSection>
      </div>
      {error ? <ErrorBanner error={error} /> : null}
    </div>
  );
}
