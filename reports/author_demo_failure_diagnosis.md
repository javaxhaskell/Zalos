# Author Demo Failure Diagnosis

Generated: 2026-05-25

## Sessions

| Demo | Session ID | Observed status | Root symptom |
|------|------------|-----------------|--------------|
| Expense exception review | `da9b613a-7626-4f25-ba6b-7be7775547b6` | `failed_other` | Orchestrator crash after pytest |
| Bank transaction categorisation | `d931c367-dbec-436c-93d8-4ef94e77dfbd` | `generated_pytest_failed` | Generated pytest + repair pathing |

---

## Failure 1 — Expense (`da9b613a`)

### Timeline

1. All four model stages completed (~51k tokens).
2. Agent executed successfully (`outputs/output.csv`, `outputs/exceptions.csv`, `reports/validation_report.md`).
3. Generated pytest: **4 pass / 1 fail** (`test_output_csv_structure`).
4. Immediately after pytest event, workflow aborted with `AttributeError: 'str' object has no attribute 'filter'`.
5. Four-tier `system_validation_report` never written; UI shows generic **Stopped**.

### Root cause

`AuthorOutputContract.summary_metrics` is typed as `list[str | SummaryMetricSpec]`. The expense contract mixes both:

- `"total_rows"` (plain string)
- structured metrics with `filter` fields (`total_exceptions`, `exceptions_by_severity`)

After pytest failure, `author_custom_build.py` still calls `validate_against_contract()` (correct — validation should run even when pytest fails). That reaches `author_contract_validation._contract_non_flagged_values()` / `_contract_flagged_values_from_metrics()`, which iterate `contract.summary_metrics` and access `metric.filter` without checking for plain strings.

**Exact failure line:** `author_contract_validation.py` lines 491–494 and 519–522 (`if not metric.filter:` on a `str` metric).

### Classification

- **Not** a session-specific issue — any contract with string `summary_metrics` entries triggers this on the post-pytest validation path.
- **Not** an agent bug for the crash; the crash prevents surfacing `generated_pytest_failed` cleanly.

---

## Failure 2 — Bank (`d931c367`)

### Timeline

1. All four model stages completed (~59k tokens).
2. Agent executed successfully — 18-row output, rich validation report with human-readable category summary table.
3. Generated pytest: **6 pass / 1 fail** (`test_validation_report_has_category_summary`).
4. Four-tier validation ran (universal + contract-specific **PASS**; generated pytest **FAIL**; golden **SKIPPED**).
5. Execution repair attempted (`failure_kind=pytest`); repair candidate staged under `generated/repairs/attempt_1/tests/test_agent.py`.
6. Repair candidate pytest: **3 failed** — including `Output CSV has no data rows` (agent invoked from wrong workspace root).
7. Final status: honest `generated_pytest_failed` with original snake_case assertion preserved.

### Root causes

**A. Generated pytest assertion mismatch (primary gate failure)**

The generated test asserts literal snake_case metric identifiers in report text:

```python
keywords = ["transaction_count", "total_debits", "total_credits", "net_amount"]
```

The agent correctly wrote a markdown table with human-readable headers (`Transaction Count`, `Total Debits`, etc.). The agent output is materially good; the **test** is overly strict. Test-generation prompts already discourage raw snake_case matches, but `_classification_test_generation_requirements()` still lists those identifiers as examples, nudging the model toward literal checks.

**B. Repair candidate workspace pathing (secondary failure)**

Repair candidates are staged at `generated/repairs/attempt_N/tests/test_agent.py` but retain `WORKSPACE_ROOT = Path(__file__).resolve().parents[2]`, which resolves to `generated/repairs/` instead of the session workspace. The repair candidate re-runs the agent against empty/wrong paths, causing cascading failures and blocking promotion.

This is **not** bank-specific — any pytest repair staged under `generated/repairs/...` with `parents[2]` breaks.

### Classification

- Primary pytest failure: **valid strict test vs good agent output** — fix via test-generation/repair guidance + repair path normalization (not by weakening validation or faking success).
- Repair failure: **structural pathing bug** in repair candidate staging.

---

## Fix plan (structural only)

| Issue | Fix |
|-------|-----|
| `metric.filter` on string metrics | Guard `summary_metrics` iteration with `isinstance(metric, SummaryMetricSpec)` helpers |
| Repair candidate wrong `WORKSPACE_ROOT` | Normalize `parents[N]` when staging repair candidate test files based on path depth |
| Snake_case report assertions | Clarify classification test-generation guidance; repair prompt already correct |
| Expense crash UX | AttributeError fix restores `generated_pytest_failed` + four-tier report on pytest-fail path |

No session-id conditionals, no bank hardcoding, no validation weakening.

---

## Fixes applied

| File | Change |
|------|--------|
| `author_contract_validation.py` | `_summary_metric_filter` / `_summary_metric_name` helpers; skip plain-string metrics when reading `.filter` |
| `author_llm_authoring.py` | Normalize `WORKSPACE_ROOT` parent depth when staging repair candidate tests; clarify classification test-gen guidance |
| `reports/author_demo_failure_diagnosis.md` | This document |
| `test_validation_layers.py` | Regression: mixed string + structured `summary_metrics` with failed pytest |
| `test_author_codegen_reliability.py` | Regression: repair candidate path depth + test-gen guidance |

**Post-fix verification:** Re-ran `validate_against_contract` against workspace `da9b613a-7626-4f25-ba6b-7be7775547b6` — completes with `overall_passed=False`, generated pytest tier FAIL (no AttributeError crash).
