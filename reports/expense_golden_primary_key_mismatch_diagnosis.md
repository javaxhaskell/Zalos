# Expense golden primary key mismatch diagnosis

**Failed session:** `518df27d-7b3b-4b05-8e8c-153bd4b78b09`  
**Error:** `GoldenDiffError: actual row missing primary_key 'row_id'`  
**Upload:** `uploads/expense_exception_review.csv` (788 bytes, 7 data rows + header)

---

## 1. What failed

Golden comparison aborted before row alignment because validation forced primary key `row_id`, while both the agent output and committed golden use `expense_id` (`EXP-001` … `EXP-007`).

Other validation tiers had already passed (schema, required columns, row count 7→7, pytest). The session stopped at Layer 5 with an exception rather than a structured golden FAIL check.

---

## 2. Failed session workspace

| Artifact | Finding |
|----------|---------|
| `generated/author_output_contract.json` | `primary_row_key: null`; output columns include `expense_id` |
| `outputs/expense_review_output.csv` | Rows keyed by `expense_id`, no `row_id` column |
| `evals/expected_output.csv` (staged) | Golden columns: `expense_id`, `review_required` |
| `events.jsonl` | `template_hint: expense_exception_review`; user message begins `Build context: Expense Exception Review` |
| `uploads/expense_exception_review.csv` | 788 bytes — matches canonical 7-row blind-eval / UI reference sample |

---

## 3. Golden files and comparison path

| Source | Primary key | Columns |
|--------|-------------|---------|
| `evals/golden/expense_exception_review/expected_output.csv` | `expense_id` | `expense_id`, `review_required` |
| Actual agent output | `expense_id` | full row-level schema incl. `expense_id` |
| Comparison code (before fix) | **`row_id` (wrong fallback)** | `author_contract_validation.py` passed `contract.primary_row_key or "row_id"` into `layer_golden_output` |

Golden staging for expense demo: `stage_expense_exception_golden()` copies repo golden to workspace `evals/expected_output.csv`; `apply_expense_exception_golden_policy()` marks golden as required but did not set `primary_row_key`.

---

## 4. Contract and `row_id` origin

| Question | Answer |
|----------|--------|
| **AuthorOutputContract.primary_row_key in failed session?** | `null` — model contract planning left it unset despite `expense_id` in input/output columns |
| **Where did `row_id` come from?** | Hard-coded fallback in `validate_against_contract`: `contract.primary_row_key or "row_id"` (line 114). Not from the model contract, golden file, or upload schema |
| **Expected golden columns** | `expense_id`, `review_required` (7 rows) |
| **Actual output columns** | 16 columns including `expense_id`; no `row_id` |
| **Correct primary key for Expense Exception Review** | `expense_id` |

---

## 5. UI label: "Finance workflow" vs "Expense Exception Review"

| Surface | Before fix | Root cause |
|---------|------------|------------|
| Run payload (`author_user_workflow` text) | `Build context: Expense Exception Review` | Correct — `buildAuthorRunMessage` + template hint |
| Session heading / dashboard (`deriveWorkItem`) | `Build context: Finance workflow` | `extractWorkflowSignalsFromEvents` ignored `decision_input` events with `kind: template_hint`; only `workflow_completed.workflow_type` was `model_authored_finance_workflow`, which does not map to expense title |

Fix: read `template_hint` events in `workflow-title.ts` so `expense_exception_review` resolves to **Expense Exception Review**.

---

## 6. Root cause (single sentence)

Golden validation used a silent `row_id` fallback when `primary_row_key` was null, while the expense demo output and golden oracle both use `expense_id`; the UI title regressed because template-hint events were not wired into workflow title resolution.

---

## 7. Fix applied

1. **`resolve_golden_primary_key()`** in `validation/golden.py` — priority: contract key → golden config key → shared ID-like column → clear user-facing error (no silent `row_id`).
2. **`layer_golden_output()`** — uses resolver; surfaces resolver failures as failed golden checks instead of uncaught exceptions.
3. **`validate_against_contract()`** — removed `or "row_id"`; passes `golden_config_primary_key` from build context.
4. **`apply_expense_exception_golden_policy()`** — sets `primary_row_key: expense_id` when unset.
5. **`author_custom_build.py`** — passes `golden_config_primary_key="expense_id"` for expense exception review context.
6. **`workflow-title.ts`** — reads `template_hint` decision events for display title.

---

## 8. Alignment checklist (788-byte canonical sample)

| Check | Status |
|-------|--------|
| UI reference sample = blind eval input (788 bytes, 7 rows) | Already aligned |
| Golden `expected_output.csv` expense_ids EXP-001…EXP-007 | Already aligned |
| Golden comparison primary key | **Fixed → `expense_id`** |
| UI build context label | **Fixed → Expense Exception Review** |

---

## 9. Post-fix fresh session verification

| Field | Value |
|-------|-------|
| Session | `9980bf32-d3f1-4b88-87ce-15768ac92e91` |
| Input | `expense_exception_review.csv` (788 bytes, template_hint) |
| Golden comparison | **PASS — 7/7 rows matched golden** (uses `expense_id`, no `row_id` error) |
| Row-level PK | `primary key 'expense_id' unique` |
| Terminal status | `failed_other` — generated pytest failures (unrelated to golden PK fix) |
| Overall GREEN | **No** — pytest tier failed; golden PK regression **fixed** |

Evidence: `reports/_fresh_expense_golden_pk_fix_run.json`, workspace `reports/system_validation_report.json`.
