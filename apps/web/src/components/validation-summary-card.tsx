"use client";

import { useMemo, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { WorkspaceEvent } from "@/lib/api-client";
import {
  VALIDATION_CHECKS_FLAT_COPY,
  VALIDATION_SUMMARY_COPY,
  deriveAuthorWorkflowContext,
} from "@/lib/ux-language";
import {
  groupChecksByTier,
  parseValidationRun,
  type CheckStatus,
  type ParsedValidationRun,
  type TierStatus,
  type ValidationCheckRow,
  type ValidationTierGroup,
} from "@/lib/validation-tiers";

/**
 * Finance-facing summary of validation layers. Newer sessions group checks by
 * backend validation_tier; older sessions fall back to a flat checklist.
 */
export interface ValidationSummaryCardProps {
  readonly events: ReadonlyArray<WorkspaceEvent>;
}

type CheckCopy = {
  title: string;
  pass: string;
  fail: string;
  skipped: string;
};

const CHECK_HUMAN: Record<string, CheckCopy> = {
  "Required deliverables present": {
    title: "Required deliverables present",
    pass: "Every output file promised by the workflow contract is present.",
    fail: "One or more required output files are missing.",
    skipped: "Deliverable presence check was skipped.",
  },
  "Output schema": {
    title: "Output column shape",
    pass: "The output contains the columns the workflow promised.",
    fail: "The output is missing one or more requested columns.",
    skipped: "Column-shape check was skipped.",
  },
  "Required columns non-null": {
    title: "Required cells populated",
    pass: "Every row has a value in every required column.",
    fail: "Some rows are missing values in required columns.",
    skipped: "Required-cell check was skipped.",
  },
  "Row-level invariants": {
    title: "Row preservation + transaction ID uniqueness",
    pass: "Row count is preserved end-to-end and transaction IDs are unique when present.",
    fail: "Row count changed or transaction IDs are duplicated.",
    skipped: "Row-preservation check was skipped.",
  },
  "Net amount arithmetic": {
    title: "Net amount arithmetic",
    pass: "Calculated net amounts match gross minus fee on every checked row.",
    fail: "At least one row has a calculated net amount that does not match gross minus fee.",
    skipped: "Net amount arithmetic check was skipped.",
  },
  "Net amount difference arithmetic": {
    title: "Net amount difference arithmetic",
    pass: "Reported net minus calculated net matches the recorded difference on every checked row.",
    fail: "At least one row has an inconsistent net amount difference.",
    skipped: "Net amount difference check was skipped.",
  },
  "Reconciliation status / issue flag rules": {
    title: "Reconciliation status / issue flag rules",
    pass: "Reconciliation statuses and issue flags follow the allowed values.",
    fail: "At least one row carries a reconciliation field outside the allowed enum.",
    skipped: "Reconciliation rule check was skipped.",
  },
  "Summary totals reconciliation": {
    title: "Summary totals reconciliation",
    pass: "Summary file totals match the row-level output for each group.",
    fail: "At least one summary total does not reconcile to the row-level output.",
    skipped: "Summary totals check was skipped.",
  },
  "Exception list consistency": {
    title: "Exception list consistency",
    pass: "The exceptions file matches row-level review flags.",
    fail: "Exception rows do not match flagged rows in the primary output.",
    skipped: "Exception list check was skipped.",
  },
  "Custom business rules": {
    title: "Reconciliation status / issue flag rules",
    pass: "Reconciliation statuses and related enum fields follow the allowed values.",
    fail: "At least one row carries a reconciliation field outside the allowed enum.",
    skipped: "Reconciliation rule check was skipped.",
  },
  "Business rules": {
    title: "Allowed categories",
    pass: "Every value in the category column is one of the allowed enum values.",
    fail: "Some rows carry a category outside the allowed enum.",
    skipped: "Allowed-categories check was skipped.",
  },
  "Allowed enum values": {
    title: "Allowed enum values",
    pass: "Every enum field uses one of the contract-approved values.",
    fail: "At least one row carries a value outside the allowed enum.",
    skipped: "Enum validation was skipped.",
  },
  "Golden output comparison": {
    title: "Independent golden comparison",
    pass: "Output matches the externally-supplied expected_output.csv.",
    fail: "Output differs from the externally-supplied expected_output.csv.",
    skipped:
      "Golden-output comparison was skipped because no independent expected-output file was supplied.",
  },
  "Generated pytest": {
    title: "Generated pytest suite",
    pass: "Every test in the generated pytest suite passed.",
    fail: "At least one test in the generated pytest suite failed.",
    skipped: "No pytest run was performed (tests directory missing).",
  },
  "Template reference parity (smoke check)": {
    title: "Reference-output smoke check",
    pass:
      "Output matches the reference sample output on the shared columns. " +
      "This is a smoke check for the bundled sample, not independent validation.",
    fail: "Output diverged from the reference sample output.",
    skipped:
      "Skipped because the upload's row count differs from the reference sample, " +
      "so a row-aligned comparison is not meaningful.",
  },
  "Semantic rules (bank categoriser)": {
    title: "Bank sample semantic rules",
    pass:
      "Refund override fires on negative amounts; subscription / travel / office / income descriptions map to the right categories.",
    fail: "At least one row breaks the documented rule cascade.",
    skipped: "Semantic-rule check was skipped (no category column).",
  },
};

const BANK_LAYER_LABELS: Record<string, string> = {
  schema: "Output column shape",
  required_columns: "Required cells populated",
  business_rules: "Allowed categories",
  row_level: "Row preservation + transaction ID uniqueness",
  golden_output: "Independent golden comparison",
  generated_pytest: "Generated pytest suite",
  template_reference: "Reference-output smoke check",
  semantic_rules: "Bank sample semantic rules",
};

const PAYMENT_LAYER_LABELS: Record<string, string> = {
  schema: "Output column shape",
  required_columns: "Required cells populated",
  row_level: "Row preservation + transaction ID uniqueness",
  golden_output: "Independent golden comparison",
  generated_pytest: "Generated pytest suite",
};

function resolveWorkflowType(
  events: ReadonlyArray<WorkspaceEvent>,
  parsed: ParsedValidationRun,
): string | null {
  if (parsed.workflowType) return parsed.workflowType;
  return deriveAuthorWorkflowContext(events).workflowType;
}

function isRawLayerKey(row: Pick<ValidationCheckRow, "checkName" | "layer">): boolean {
  return row.checkName === row.layer;
}

function resolveCheckTitle(
  row: Pick<ValidationCheckRow, "checkName" | "layer">,
  workflowType: string | null,
): string {
  const copy = CHECK_HUMAN[row.checkName];
  if (copy) return copy.title;

  if (isRawLayerKey(row)) {
    if (workflowType === "payment_processor_reconciliation") {
      return PAYMENT_LAYER_LABELS[row.layer] ?? row.checkName;
    }
    return BANK_LAYER_LABELS[row.layer] ?? row.checkName;
  }

  return row.checkName;
}

function resolveCheckCopy(row: ValidationCheckRow): CheckCopy | undefined {
  return CHECK_HUMAN[row.checkName];
}

function enrichChecks(
  checks: readonly ValidationCheckRow[],
  workflowType: string | null,
): ValidationCheckRow[] {
  const seenTitles = new Set<string>();
  const enriched: ValidationCheckRow[] = [];

  for (const check of checks) {
    const displayTitle = resolveCheckTitle(check, workflowType);
    if (seenTitles.has(displayTitle)) continue;
    seenTitles.add(displayTitle);
    enriched.push({ ...check, displayTitle });
  }

  return enriched;
}

function deriveOverallBadge(checks: readonly ValidationCheckRow[]) {
  const failed = checks.filter((check) => check.status === "fail");
  const skipped = checks.filter((check) => check.status === "skipped");
  const overallPassed = checks.every(
    (check) => check.status === "pass" || check.status === "skipped",
  );

  if (!overallPassed) {
    return <Badge variant="danger">{failed.length} check(s) failed</Badge>;
  }
  if (skipped.length > 0) {
    return <Badge variant="success">All required checks passed</Badge>;
  }
  return <Badge variant="success">All checks passed</Badge>;
}

export function ValidationSummaryCard({ events }: ValidationSummaryCardProps) {
  const parsed = useMemo(() => parseValidationRun(events), [events]);
  const workflowType = useMemo(
    () => (parsed ? resolveWorkflowType(events, parsed) : null),
    [events, parsed],
  );
  const checks = useMemo(
    () => (parsed ? enrichChecks(parsed.checks, workflowType) : []),
    [parsed, workflowType],
  );

  if (!parsed || checks.length === 0) return null;

  if (parsed.hasTierMetadata) {
    const tiers = groupChecksByTier(checks);
    return (
      <TieredValidationView
        tiers={tiers}
        overallBadge={deriveOverallBadge(checks)}
      />
    );
  }

  return (
    <FlatValidationView checks={checks} overallBadge={deriveOverallBadge(checks)} />
  );
}

function TieredValidationView({
  tiers,
  overallBadge,
}: {
  readonly tiers: readonly ValidationTierGroup[];
  readonly overallBadge: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>{VALIDATION_SUMMARY_COPY.title}</span>
          {overallBadge}
        </CardTitle>
        <CardDescription>{VALIDATION_SUMMARY_COPY.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {tiers.map((tier) => (
          <section
            key={tier.tier}
            className="rounded-lg border border-slate-200 bg-slate-50/60 p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-semibold text-slate-900">{tier.title}</h3>
                <p className="mt-1 text-xs text-slate-600">{tier.description}</p>
              </div>
              <TierBadge status={tier.status} checkCount={tier.checks.length} />
            </div>
            <ul className="mt-3 space-y-2">
              {tier.checks.map((check) => (
                <CheckRow key={check.key} check={check} />
              ))}
            </ul>
          </section>
        ))}
      </CardContent>
    </Card>
  );
}

function FlatValidationView({
  checks,
  overallBadge,
}: {
  readonly checks: readonly ValidationCheckRow[];
  readonly overallBadge: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>{VALIDATION_CHECKS_FLAT_COPY.title}</span>
          {overallBadge}
        </CardTitle>
        <CardDescription>{VALIDATION_CHECKS_FLAT_COPY.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm">
          {checks.map((check) => (
            <CheckRow key={check.key} check={check} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function CheckRow({ check }: { readonly check: ValidationCheckRow }) {
  const copy = resolveCheckCopy(check);
  const summary = copy
    ? copy[check.status]
    : check.status === "pass"
      ? "Passed."
      : check.status === "fail"
        ? "Failed."
        : "Skipped.";

  return (
    <li
      className={[
        "flex items-start gap-3 rounded-md border px-3 py-2",
        check.status === "fail"
          ? "border-red-200 bg-red-50"
          : "border-slate-200 bg-white",
      ].join(" ")}
    >
      <CheckBadge status={check.status} />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-slate-900">{check.displayTitle}</p>
        <p className="mt-0.5 text-xs text-slate-700">{summary}</p>
        {check.evidence ? (
          <p className="mt-1 text-xs text-slate-500">Evidence: {check.evidence}</p>
        ) : null}
      </div>
    </li>
  );
}

function TierBadge({
  status,
  checkCount,
}: {
  readonly status: TierStatus;
  readonly checkCount: number;
}) {
  const label = `${status.toUpperCase()} · ${checkCount} check${checkCount === 1 ? "" : "s"}`;
  if (status === "pass") return <Badge variant="success">{label}</Badge>;
  if (status === "fail") return <Badge variant="danger">{label}</Badge>;
  if (status === "warning") return <Badge variant="warning">{label}</Badge>;
  return <Badge variant="neutral">{label}</Badge>;
}

function CheckBadge({ status }: { readonly status: CheckStatus }) {
  if (status === "pass") return <Badge variant="success">PASS</Badge>;
  if (status === "fail") return <Badge variant="danger">FAIL</Badge>;
  return <Badge variant="neutral">SKIPPED</Badge>;
}
