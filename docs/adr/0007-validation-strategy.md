# ADR-0007 — Validation strategy

**Status:** accepted
**Date:** 2026-05-21

## Context

The assignment requires "tests or golden-output checks." More importantly, the finance-user pitch requires validation that proves output correctness, not just process completion. "The script ran" is not a meaningful finance check; it must be supplemented with structural and semantic correctness.

## Decision

**Author flow — six-layer validation:**

1. **Schema** — output file exists; required columns present; per-column dtypes match `AuthorRequirements.expected_output_columns`.
2. **Required columns** — computed columns non-null on every row; primary key preserved 1:1 from input.
3. **Business rules** — per-domain invariants from `AuthorRequirements.business_rules` (e.g., negative-amount rows are `Refund`; categories in allowed enum; Uncategorised rate < 25%).
4. **Row-level** — pairwise invariants (sum of input amounts equals sum of output amounts within tolerance; row counts preserved).
5. **Golden output** — row-aligned comparison against the committed golden CSV on the primary key; per-column tolerance for floats.
6. **Generated pytest** — the agent's own generated `tests/test_agent.py` runs and passes.

Result: `ValidationReport` (markdown + JSON sidecar) with per-layer pass/fail + one-line evidence + expandable detail.

**Repair flow — six pieces of evidence:**

1. **Reproduction** — `ReproductionResult` showing the bug manifests (failing pytest test, or sample-run output differing from expected).
2. **Failing test / wrong output** — specific test name + failure excerpt, or specific row(s) differing from expected.
3. **Root cause** — `Diagnosis` naming file + line + plain-English explanation.
4. **Patch** — unified diff (file path + line range) + model's rationale.
5. **After-fix run** — same pytest invocation showing previously-failing test now passes; no new failures.
6. **Expected-output comparison** — golden diff against fixture's `data/expected_output.csv` (if present) showing zero differences on the bug-affected column.

Result: `RepairReport` (markdown + JSON sidecar) with six structured sections plus "remaining risks" and "recommended next steps."

## Options considered

| Option | Verdict |
|---|---|
| **6-layer author + 6-piece repair** | Selected |
| Single-check (exit code only) | Insufficient; "the script ran" is not a finance check |
| 3-layer author (schema + golden + pytest) | Loses business-rule visibility; rejected |
| 9+ layers with adversarial robustness checks | Over-engineered for prototype; future production extension |

## Rationale

- Each layer answers a distinct question:
  - Schema: structural correctness.
  - Required columns: completeness.
  - Business rules: domain correctness.
  - Row-level: aggregation correctness.
  - Golden: end-to-end correctness against a known reference.
  - Pytest: the agent's own tests, which double as regression protection for the user post-handoff.
- All six layers pass simultaneously when the agent is correct; partial passes surface specific defect categories.
- The `ValidationReport` is the operator-facing evidence of correctness; a operators can read it and judge correctness without reading the code.
- The `RepairReport` mirrors the structure expected by an enterprise change-management process: problem, evidence, diagnosis, fix, validation, residual risk.

## Consequences

- The validation engine (`apps/api/src/agentforge/validation/`) implements the six layers as composable check functions.
- Every author scenario in the eval suite asserts all six layers pass.
- Every repair scenario asserts all six pieces of evidence present.
- The frontend's `TestResultsPanel` renders the report with humanised test names (not raw `test_aging_april_invoices`).
- "The script ran" is never accepted as a check anywhere.

## Reversal condition

- Add adversarial-robustness layers (paraphrase robustness, prompt-injection refusal) as a separate suite when scale demands.
- Reduce to fewer layers only if specific layers prove non-discriminating across the eval set (none have, in the prototype scope).
