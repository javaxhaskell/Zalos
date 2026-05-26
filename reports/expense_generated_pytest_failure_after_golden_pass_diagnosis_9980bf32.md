# Expense Generated Pytest Failure After Golden Pass: 9980bf32-d3f1-4b88-87ce-15768ac92e91

**Session:** `9980bf32-d3f1-4b88-87ce-15768ac92e91`  
**Workspace:** `.workspaces/9980bf32-d3f1-4b88-87ce-15768ac92e91`  
**Terminal status:** `failed_other` — generated pytest 2 failed / 4 passed; golden **PASS 7/7**; four-tier validation failed only on generated pytest tier.

---

## 1. What failed

| Layer | Result |
| --- | --- |
| Agent execution | PASS (exit 0) |
| Deterministic validation (schema, columns, enums, exception list) | PASS |
| Golden output comparison | **PASS — 7/7 rows** (`expense_id` PK) |
| Generated pytest | **FAIL — 2/6 tests** |

Failed tests:

1. `generated/tests/test_agent.py::test_allowed_enums`
2. `generated/tests/test_agent.py::test_exception_rules_logic`

System validation evidence: `2 failed / 4 passed` in `reports/system_validation_report.json` (pytest summary counts exclude some collected tests in evidence string; 6 tests collected).

---

## 2. Failed session workspace

| Artifact | Finding |
| --- | --- |
| `uploads/expense_exception_review.csv` | 788 bytes, 7 data rows (canonical reference sample) |
| `generated/author_output_contract.json` | 3 `exception_rules` (amount_over_limit, missing_receipt, approval_not_final); `allowed_enums` lists exception_flag, review_required, severity only — **no rule_used enum** |
| `generated/agent.py` | Implements 4 rules including `suspicious_keywords` on notes (from codegen guidance) |
| `generated/tests/test_agent.py` | Re-implements 3 contract rules with exact reason prose; hardcodes `rule_used` allowed set |
| `outputs/output.csv` | 7 rows; EXP-005 flagged via suspicious keywords |
| `evals/expected_output.csv` | Golden: EXP-005 `review_required=yes` |
| `events.jsonl` | `failure_kind=pytest` repair attempt 1; candidate 1 failed / 5 passed |

---

## 3. Golden vs pytest divergence

| expense_id | Golden `review_required` | Agent output | Contract-only test expected |
| --- | --- | --- | --- |
| EXP-005 | **yes** | review_required (suspicious_keywords) | **no_issue** (no contract rule fires) |

Golden compares only `expense_id` + `review_required` — agent matches golden 7/7.  
Pytest `test_exception_rules_logic` re-derived expectations from **contract-only** rules with exact `exception_reason` strings, so EXP-005 failed despite golden PASS.

---

## 4. Contract and codegen alignment

| Source | suspicious_notes / keywords rule |
| --- | --- |
| Contract `exception_rules` | **Absent** (3 rules only) |
| `_expense_exception_schema_guidance` planning | Candidate `suspicious_notes` rule documented |
| `_exception_review_codegen_section` | Instructs keyword scan on notes (`personal`, `duplicate`, …) |
| Contract review rules | Require notes-based rules when schema has notes — **model omitted in this session** |
| Golden oracle | EXP-005 expects review (notes: "personal weekend travel") |

**Gap:** Contract planning/review left out `suspicious_notes`, while codegen guidance and golden expect notes-based flagging.

---

## 5. Generated pytest behavior (root failure mode)

### `test_allowed_enums`

Hardcoded:

```python
"rule_used": {"none", "amount_over_limit", "missing_receipt", "approval_not_final"}
```

`rule_used` is **not** in `contract.allowed_enums`. Agent emitted `suspicious_keywords` for EXP-005 → enum assertion failed.

### `test_exception_rules_logic`

`compute_expected_exception()` implements only the 3 contract rules with **exact** `exception_reason` prose copied from contract. Production upload rows compared field-for-field including `exception_reason` and `rule_used`.

**Assertion (EXP-005):**

```text
Expected 'no_issue', got 'review_required'
```

This is a **test expectation bug** relative to golden + agent output, not an agent regression.

---

## 6. Agent output quality

| Check | Status |
| --- | --- |
| Row count 7→7 | PASS |
| Required columns non-null | PASS |
| Exception list consistency (5 rows) | PASS |
| Golden review_required | PASS 7/7 |
| Contract-only rule_used enum | Agent uses `suspicious_keywords` (codegen-aligned, contract-incomplete) |

Agent behavior is **acceptable for demo/golden**; pytest failed due to brittle generated tests, not missing artifacts.

---

## 7. Repair attempts

| Attempt | `failure_kind` | Target | Result |
| --- | --- | --- | --- |
| 1 | `pytest` | `generated/repairs/attempt_1/tests/test_agent.py` | 1 failed / 5 passed |

Repair added `suspicious_keywords` to test helper but kept exact `exception_reason` equality — still failed on prose mismatch (`Notes contain suspicious keyword(s): personal.` vs `Notes contain suspicious keywords.`).

Routing to test-only repair was **correct**; repair prompt lacked expense-specific guidance to drop prose equality on production rows.

---

## 8. Root cause classification

**Primary: F — Mixed issue**

| Sub-class | Description |
| --- | --- |
| **B** | Generated pytest brittleness — invented `rule_used` enum, exact `exception_reason` prose on production upload |
| **D** | Contract planning gap — `suspicious_notes` omitted from `exception_rules` while golden/codegen expect it |
| **E** | Repair prompt gap — no expense-specific pytest repair requirements for stable-field assertions |

Not primary:

- **A** — Validation layer bug (validation passed; pytest tier failed honestly)
- **C** — Agent output wrong vs golden (golden 7/7 PASS)
- Pytest output mutation — not observed; failure messages match local reproduction

---

## 9. Fix applied (general, future sessions)

1. **`_expense_exception_test_generation_requirements`** — prefer stable fields (`exception_flag`, `review_required`, `severity`, `rule_used`); forbid exact `exception_reason` prose on production rows; derive `rule_used` from contract `exception_rules` only; apply `allowed_enums` only to listed columns.
2. **`_expense_exception_pytest_repair_requirements`** — injected on brittle expense pytest failures during repair.
3. **`_pytest_signals_expense_brittle_test_failure`** + **`_pytest_failure_repair_kind`** — route invented enum / prose mismatches to test-only repair.
4. **Contract review guidance** — strengthen notes-based `exception_rules` requirement when notes column present.
5. **Fixture + regression tests** — `expense_generated_pytest_failure_after_golden_pass_9980bf32.json`.

Golden primary-key path (`expense_id`) unchanged — `test_expense_golden_comparison_uses_expense_id_not_row_id` remains valid.

---

## Artifact paths inspected

- `.workspaces/9980bf32-d3f1-4b88-87ce-15768ac92e91/events.jsonl`
- `generated/author_output_contract.json`, `generated/agent.py`, `generated/tests/test_agent.py`
- `generated/model_responses/test_generation.prompt.txt`, `test_generation.txt`
- `generated/model_responses/execution_repair_1.prompt.txt`, `execution_repair_1.txt`
- `outputs/output.csv`, `outputs/exceptions.csv`
- `evals/expected_output.csv`, `reports/system_validation_report.json`
