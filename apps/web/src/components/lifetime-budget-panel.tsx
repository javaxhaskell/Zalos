"use client";

import { useEffect, useState } from "react";

import { CollapsibleSection } from "@/components/collapsible-section";
import { ErrorBanner } from "@/components/error-banner";
import { SkeletonLine } from "@/components/motion";
import { getBudgetSummary } from "@/lib/api-client";
import { cn } from "@/lib/utils";

type LifetimeBudgetTotals = {
  lifetime_tokens_used: number;
  session_count: number;
};

function formatTokens(value: number): string {
  return value.toLocaleString();
}

export function LifetimeBudgetPanel() {
  const [summary, setSummary] = useState<LifetimeBudgetTotals | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getBudgetSummary();
        if (!cancelled) {
          setSummary({
            lifetime_tokens_used: data.lifetime_tokens_used,
            session_count: data.session_count,
          });
        }
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const title = "Lifetime usage";
  const description = "Total model tokens consumed across all saved sessions.";

  if (loading) {
    return (
      <CollapsibleSection title={title} description={description} className="mt-8">
        <SkeletonLine className="h-4 w-48" />
        <SkeletonLine className="mt-3 h-3 w-full max-w-md" />
      </CollapsibleSection>
    );
  }

  if (error) {
    return (
      <CollapsibleSection title={title} description={description} className="mt-8">
        <ErrorBanner error={error} />
      </CollapsibleSection>
    );
  }

  if (!summary) return null;

  const totalLabel = formatTokens(summary.lifetime_tokens_used);

  return (
    <CollapsibleSection title={title} description={description} className="mt-8">
      <div>
        <div className="mb-1 flex items-baseline justify-between text-xs">
          <span className="font-medium text-slate-700">Tokens used (all sessions)</span>
          <span className="font-mono text-slate-600">{totalLabel}</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded bg-slate-200">
          <div
            className={cn(
              "h-full bg-emerald-500",
              summary.lifetime_tokens_used > 0 ? "min-w-[2%]" : "w-0",
            )}
            style={{ width: summary.lifetime_tokens_used > 0 ? "100%" : "0%" }}
          />
        </div>
        <p className="mt-2 text-xs text-slate-600">
          {summary.session_count.toLocaleString()} session
          {summary.session_count === 1 ? "" : "s"} tracked. Soft-deleted sessions are
          excluded.
        </p>
      </div>
    </CollapsibleSection>
  );
}
