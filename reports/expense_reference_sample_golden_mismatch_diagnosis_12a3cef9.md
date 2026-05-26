# Expense reference sample / golden mismatch diagnosis

**Session:** `12a3cef9-4413-42a2-b288-2773f7dbb1f6`  
**Status:** `stopped` — pytest 5/5 PASS, golden FAIL (4 divergences), other validation PASS  
**Green baseline:** `98942274-b4fb-4e0d-a3b8-0b8144bc01db` — pytest 5/5 PASS, golden 7/7 PASS

---

## 1. Row counts

| Source | Rows | expense_ids |
|--------|------|-------------|
| Failed session input (`uploads/expense_exception_review.csv`) | **8** | EXP-001 … EXP-008 |
| Green session input (`uploads/input.csv`) | **7** | EXP-001 … EXP-007 |
| Golden (`evals/golden/expense_exception_review/expected_output.csv`) | **7** | EXP-001 … EXP-007 |
| Blind eval canonical (`blind_eval_cases/expense_exception_review/input.csv`) | **7** | EXP-001 … EXP-007 |
| UI reference sample (before fix) | **8** | EXP-001 … EXP-008 |
| Failed session output | **8** | EXP-001 … EXP-008 |

**Mismatch:** yes — the failed session used an 8-row UI reference sample while golden and green session use the 7-row blind-eval canonical input.

---

## 2. Input comparison

### Failed session (12a3cef9)

- **Entry:** UI template **Expense Exception Review** → bundled reference sample
- **File:** `uploads/expense_exception_review.csv`
- **Schema:** GBP demo columns (`employee_name`, `receipt_available`, `manager_approved`, `description`)
- **Extra row:** EXP-008 (James Wilson — over limit, missing receipt, not approved)

### Green session (98942274)

- **Entry:** custom upload of blind-eval CSV
- **File:** `uploads/input.csv`
- **Schema:** USD blind-eval columns (`approval_status`, `receipt_attached`, `notes`)
- **Rows:** 7 — matches golden exactly

### Golden oracle

Hand-verified `review_required` for the **7-row blind-eval sample** only:

| expense_id | review_required | Reason (blind-eval data) |
|------------|-----------------|--------------------------|
| EXP-001 | yes | 120 > 100 policy limit |
| EXP-002 | yes | receipt false, amount > 100 |
| EXP-003 | no | within policy, receipt ok |
| EXP-004 | yes | approval pending |
| EXP-005 | yes | notes contain "personal" |
| EXP-006 | no | clean row |
| EXP-007 | yes | over limit + missing receipt + rejected + suspicious notes |

---

## 3. Golden divergences explained

Failed session golden detail:

```
1 extra rows
EXP-001.review_required='no'≠'yes'
EXP-003.review_required='yes'≠'no'
EXP-005.review_required='no'≠'yes'
```

These are **not agent logic bugs on shared semantics** — they are **different input facts** for the same expense_id keys:

| ID | 8-row reference input | Agent output | Golden (7-row) |
|----|----------------------|--------------|----------------|
| EXP-001 | 86.20 ≤ 150 limit, receipt Y | no | yes (120 > 100) |
| EXP-003 | receipt N, 45.50 ≤ 75 | yes | no (receipt ok) |
| EXP-005 | normal software expense | no | yes ("personal" in notes) |
| EXP-008 | present | yes | **absent from golden** |

Row-level invariant passed (`8 → 8`) because input and output both had 8 rows. Golden failed because it compares against a **7-row oracle keyed by expense_id**.

Agent behaviour on the 8-row reference data was internally consistent (5/5 pytest, exception list consistency PASS). The failure is **oracle/input pairing**, not codegen quality on the wrong dataset.

---

## 4. Root cause

**Primary: A — sample mismatch (combination with D — route drift)**

| Class | Verdict |
|-------|---------|
| A Sample mismatch | **Yes** — UI reference CSV was an unrelated 8-row GBP demo |
| B Agent error | **No** — agent correctly processed the uploaded 8-row file |
| C Golden error | **No** — golden matches blind-eval oracle and green session |
| D Route drift | **Yes** — frontend `/api/reference-samples/expense-exception-review` served a different file than golden lookup (`evals/golden/...` tied to blind eval) |
| E Combination | **A + D** |

Golden staging is unconditional for expense-exception context (`stage_expense_exception_golden` always copies the 7-row golden). Any entry path that uploads a different CSV will fail golden even when pytest and contract checks pass.

---

## 5. Canonical entry points (before fix)

| Entry point | File | Rows | Matched golden? |
|-------------|------|------|-----------------|
| `blind_eval_cases/expense_exception_review/input.csv` | blind eval | 7 | yes |
| Green session upload | same content | 7 | yes |
| `evals/golden/expense_exception_review/expected_output.csv` | oracle | 7 | — |
| `apps/web/.../expense_exception_review.csv` | UI reference | **8** | **no** |
| `demo/SCRIPT.md`, README | document both paths | mixed | drift |
| Failed session 12a3cef9 | UI reference | 8 | no |

---

## 6. Fix applied

**Smallest safe change:** restore UI reference sample to byte-match `blind_eval_cases/expense_exception_review/input.csv`. Golden unchanged.

### Files changed

| File | Change |
|------|--------|
| `apps/web/app/api/reference-samples/expense-exception-review/expense_exception_review.csv` | Replaced 8-row GBP demo with 7-row blind-eval canonical input |
| `apps/api/tests/test_reference_sample_honesty.py` | Updated schema profile to 7 rows; added pairing consistency tests |
| `apps/web/src/components/feature-walkthrough/constants.ts` | Updated walkthrough metadata to 7-row blind-eval schema |

### Tests added

- `test_expense_reference_sample_matches_blind_eval_canonical_input`
- `test_expense_golden_pairs_with_canonical_reference_sample`

Golden validation was **not** weakened.

---

## 7. Verification commands

```bash
python -m compileall apps/api/src apps/api/tests -q
cd apps/api && python -m pytest tests/test_final_demo_output_quality.py tests/test_reference_sample_honesty.py tests/test_validation_layers.py -q
cd apps/web && npm run typecheck && npm run build
```

See command results section below after run.

### Results (2026-05-26)

```
python -m compileall apps/api/src apps/api/tests -q   → exit 0
pytest … test_final_demo_output_quality.py
         … test_reference_sample_honesty.py
         … test_validation_layers.py                  → 55 passed
cd apps/web && npm run typecheck                       → exit 0
cd apps/web && npm run build                           → exit 0
```

Fresh Author rerun: **not executed** (optional; pairing fix verified by new consistency tests; full LLM Author run ~3–5 min).

---

## 8. Expected outcome after fix

Sessions started via **Expense Exception Review** reference sample or blind-eval upload should both:

- ingest 7 rows
- stage the same golden
- compare `review_required` on EXP-001 … EXP-007 only

Re-running session `12a3cef9` is **not** required (archived failure remains honest evidence of the pre-fix drift).
