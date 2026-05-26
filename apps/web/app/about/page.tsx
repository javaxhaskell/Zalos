import Link from "next/link";

import { FeatureWalkthrough } from "@/components/feature-walkthrough";

export const dynamic = "force-static";

/**
 * In-app user guide for finance teams using AgentForge.
 */
export default function AboutPage() {
  return (
    <>
      <main className="mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8">
          <p className="mb-2 text-sm">
            <Link
              href="/"
              className="text-slate-500 hover:text-slate-900 hover:underline"
            >
              ← Back to dashboard
            </Link>
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
            How it works
          </h1>
        </header>

        <div className="space-y-6">
          <section className="-mx-2 rounded-xl border border-slate-200/80 bg-white px-4 py-8 shadow-sm sm:-mx-4 sm:px-8">
            <FeatureWalkthrough title="Author workflow" variant="author" />
          </section>

          <section className="-mx-2 rounded-xl border border-slate-200/80 bg-white px-4 py-8 shadow-sm sm:-mx-4 sm:px-8">
            <FeatureWalkthrough title="Repair workflow" variant="repair" />
          </section>
        </div>
      </main>
    </>
  );
}
