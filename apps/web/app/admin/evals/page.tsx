"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { EvalCaseRow } from "@/components/eval-case-row";
import { EvalRunSummaryCard } from "@/components/eval-run-summary";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TBody, TH, THead, TR, Table } from "@/components/ui/table";
import {
  ApiError,
  type EvalRunSummary,
  getLatestEvalRun,
  runEvals,
} from "@/lib/api-client";

export const dynamic = "force-dynamic";

/**
 * /admin/evals — eval-runner dashboard (BP10b).
 *
 * Loads the most-recent ``EvalRunSummary`` from ``GET /evals/latest``
 * on mount; a "Run evals" button POSTs to ``/evals/run`` and replaces
 * the rendered summary on success. Read-only otherwise — every action is a single button.
 */
export default function AdminEvalsPage() {
  const [summary, setSummary] = useState<EvalRunSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [empty, setEmpty] = useState(false);

  const loadLatest = useCallback(async () => {
    try {
      const latest = await getLatestEvalRun();
      setSummary(latest);
      setEmpty(false);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setEmpty(true);
        setError(null);
        return;
      }
      setError(err);
    }
  }, []);

  useEffect(() => {
    void loadLatest();
  }, [loadLatest]);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      const result = await runEvals();
      setSummary(result);
      setEmpty(false);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <main className="mx-auto max-w-5xl px-6 py-10">
        <nav className="mb-4 text-sm text-slate-500">
          <Link href="/" className="hover:underline">
            ← Back to dashboard
          </Link>
        </nav>
        <header className="mb-6 flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              Eval runs
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Runs the bundled scenarios (author / repair / adversarial)
              against scripted model clients. Lets you check the agent
              loop + tool registry + validator stay correct as the
              codebase changes.
            </p>
          </div>
          <Button
            type="button"
            variant="primary"
            disabled={busy}
            onClick={onRun}
          >
            {busy ? "Running evals…" : "Run evals now"}
          </Button>
        </header>

        {error ? <ErrorBanner error={error} /> : null}

        {summary ? (
          <div className="space-y-6">
            <EvalRunSummaryCard summary={summary} />
            <Card>
              <CardHeader>
                <CardTitle>Per-scenario results</CardTitle>
                <CardDescription>
                  One row per scenario. Failing rows surface the
                  terminal-status mismatch so you can jump to the
                  scenario JSON and the script.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <THead>
                    <TR>
                      <TH>Scenario</TH>
                      <TH className="w-20">Result</TH>
                      <TH className="w-32">Latency</TH>
                      <TH>Failure</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {(summary.results ?? []).map((r) => (
                      <EvalCaseRow key={r.scenario_id} result={r} />
                    ))}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        ) : empty ? (
          <Alert variant="info">
            <AlertTitle>No eval runs yet</AlertTitle>
            <AlertDescription>
              Click <strong>Run evals now</strong> to drive the three
              bundled scenarios. Results land in the database and
              render here.
            </AlertDescription>
          </Alert>
        ) : (
          <p className="text-sm text-slate-500">Loading latest run…</p>
        )}

        <footer className="mt-10 text-xs text-slate-500">
          Scenarios are committed under{" "}
          <code className="font-mono">evals/scenarios/</code>; the
          FakeModelClient scripts live in{" "}
          <code className="font-mono">
            apps/api/src/agentforge/evals/scripts.py
          </code>
          .
        </footer>
      </main>
    </>
  );
}
