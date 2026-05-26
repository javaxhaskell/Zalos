import Link from "next/link";

import { AnimatedSection, PageShell } from "@/components/motion";
import { LifetimeBudgetPanel } from "@/components/lifetime-budget-panel";
import { RecentSessions } from "@/components/recent-sessions";
import { RunningProcessesPanel } from "@/components/running-processes-panel";
import { WorkflowPicker } from "@/components/workflow-picker";

export const dynamic = "force-dynamic";

export default function Dashboard() {
  return (
    <>
      <PageShell>
        <main className="mx-auto max-w-6xl px-6 py-10">
          <header className="mb-8">
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
              AgentForge
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
              Describe a finance workflow or upload an existing agent. Watch progress,
              return to saved sessions from the list below, and download full outputs
              with an audit trail.
            </p>
            <p className="mt-3 max-w-3xl text-sm text-slate-600">
              New here?{" "}
              <Link
                href="/about"
                className="font-medium text-slate-900 underline decoration-slate-400 underline-offset-4 hover:decoration-slate-700"
              >
                Learn how it works →
              </Link>
            </p>
          </header>

          <section aria-label="Start a workflow">
            <WorkflowPicker />
          </section>

          <AnimatedSection index={1} className="mt-8 block">
            <RunningProcessesPanel />
          </AnimatedSection>

          <AnimatedSection index={2} className="mt-8 block">
            <RecentSessions />
          </AnimatedSection>

          <AnimatedSection index={3} className="mt-8 block">
            <LifetimeBudgetPanel />
          </AnimatedSection>
        </main>
      </PageShell>
    </>
  );
}
