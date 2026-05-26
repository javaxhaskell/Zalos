# Expense Multi-Rule Enum and Exception Consistency Diagnosis: eed97570-e992-4b67-bce6-01dbe8051e2a

**Session:** `eed97570-e992-4b67-bce6-01dbe8051e2a`  
**Workspace:** `.workspaces/eed97570-e992-4b67-bce6-01dbe8051e2a`  
**Terminal status:** `author_validation_failed` — generated pytest **2 failed / 3 passed**; golden **PASS 7/7**; universal PASS; contract-specific FAIL on allowed enum + exception list consistency.

---

## 1. What failed?

| Layer | Result |
| --- | --- |
| Agent execution | PASS (exit 0) |
| Universal validation (schema, row count, PK) | PASS |
| Allowed enum values | **FAIL** — `row 8: rule_used='amount_over_limit; missing_receipt; approval_not_final; suspicious_notes'` |
| Exception list consistency | **FAIL** — `exception count 1 != flagged rows 5` |
| Golden output comparison | **PASS — 7/7 rows** (`expense_id` PK, `review_required`) |
| Generated pytest | **FAIL — 2/5 tests** |

Failed tests:

1. `generated/tests/test_agent.py::test_allowed_enums`
2. `generated/tests/test_agent.py::test_exception_rule_amount_over_limit`

---

## 2. What does the multi-rule row evidence show?

| Field | EXP-007 value |
| --- | --- |
| `exception_flag` | `exception` |
| `review_required` | `yes` |
| `rule_used` | `amount_over_limit; missing_receipt; approval_not_final; suspicious_notes` |
| `exception_reason` | Four contract reasons joined with `; ` |
| Input triggers | amount 500 > limit 400; receipt false; approval rejected; notes contain duplicate + cash advance |

The agent correctly fires all four contract `exception_rules` on one row and joins identifiers the same way it joins reasons. This matches codegen guidance to combine multiple hits.

---

## 3. Is agent output correct vs golden?

| Check | Status |
| --- | --- |
| Row count 7→7 | PASS |
| Required columns non-null | PASS |
| Golden `review_required` | **PASS 7/7** |
| Multi-rule EXP-007 | Golden expects review; agent flags review — aligned |

Golden ignores unstable columns (`exception_flag`, `exception_reason`, `severity`, `rule_used`). Agent behaviour is acceptable for demo/golden; failures are validation/test semantics, not missing artifacts.

---

## 4. Why did exception list consistency fail?

| Question | Answer |
| --- | --- |
| Rows in `outputs/output.csv` flagged | 5 (`exception_flag=exception`) |
| Rows in `outputs/exceptions.csv` at validation time | **1** (`TEST-001` synthetic fixture row) |
| Agent initial execution | Wrote 5 exception rows correctly |
| Sequence | Pytest ran **before** deterministic validation (`events.jsonl` L46–L50) |
| Corruption mechanism | `test_exception_rule_amount_over_limit` ran agent on synthetic input but agent always writes `outputs/exceptions.csv`; test expected `outputs/test_fixture_exceptions.csv` and did not restore production exceptions before validation |

**Primary:** pytest side-effect left production `exceptions.csv` inconsistent with flagged output rows.

---

## 5. Why did allowed enum fail?

| Question | Answer |
| --- | --- |
| Contract `allowed_enums.rule_used` | Single-token list: `amount_over_limit`, `missing_receipt`, `approval_not_final`, `suspicious_notes`, `none` |
| Validator behaviour | Exact whole-string membership in `_layer_allowed_enums` |
| Agent behaviour | Semicolon-separated multi-rule token string on EXP-007 |
| Generated pytest | Same exact-membership check in `test_allowed_enums` |

**Primary:** validation and testgen treated `rule_used` as a scalar enum though multi-rule rows are expected when several `exception_rules` fire.

---

## 6. What is the generated pytest failure mode?

### `test_allowed_enums`

```python
assert ru in allowed_rule_used  # fails on EXP-007 multi-rule string
```

### `test_exception_rule_amount_over_limit`

- Runs agent with alternate `temp_input` / `temp_output` but **no alternate exceptions path**
- Reads nonexistent `outputs/test_fixture_exceptions.csv` → `FileNotFoundError`
- Overwrites production `outputs/exceptions.csv` with one synthetic row before validation

Both are test/validator brittleness relative to correct agent output and golden PASS.

---

## 7. Root cause classification and chosen design

**Classification: F — Mixed issue**

| Sub-class | Description |
| --- | --- |
| **A** | Validation layer too strict — scalar enum check on multi-token `rule_used` |
| **B** | Generated pytest brittleness — exact `rule_used` equality; synthetic test corrupts production exceptions file |
| **E** | Pipeline ordering — deterministic validation reads outputs after pytest without restoring exceptions |

Not primary:

- **C** — Agent wrong vs golden (golden 7/7 PASS)
- **D** — Contract planning gap (contract includes all four rules and `rule_used` enum)

### Chosen design: **Option A (rule_used free-text composed of known tokens)**

Least disruptive given current schema:

- Keep `rule_used` in `allowed_enums` with individual rule ids
- Treat values containing `;` as semicolon-separated lists; validate **each token** against the allowed set (containment, not whole-string equality)
- Do **not** loosen other enum columns (`exception_flag`, `review_required`, `severity`)
- Restore production `outputs/exceptions.csv` after pytest when flagged-row counts diverge
- Testgen: same containment rule; synthetic tests must restore both `output.csv` and `exceptions.csv`

Options B (`rules_triggered`) and C (`primary_rule + rules_triggered`) would require contract schema migration; unnecessary for this workflow.

---

## Fix applied (general, future sessions)

1. **`_layer_allowed_enums`** — semicolon-token validation for `rule_used`
2. **`layer_business_rules`** — matching token validation for `rule_used`
3. **`production_outputs_need_agent_restore`** — rerun agent when exceptions count/PK diverges after pytest
4. **`_expense_exception_test_generation_requirements`** — multi-rule containment + restore both CSVs
5. **`_expense_exception_pytest_repair_requirements`** — same containment guidance
6. **`_exception_review_codegen_section`** — document multi-rule join + full exceptions file population
7. **Fixture + regression tests** — `expense_multi_rule_enum_exception_consistency_eed97570.json`

Golden primary-key path unchanged — stays strict on stable fields only.

---

## Artifact paths inspected

- `.workspaces/eed97570-e992-4b67-bce6-01dbe8051e2a/events.jsonl`
- `generated/author_output_contract.json`, `generated/agent.py`, `generated/tests/test_agent.py`
- `generated/model_responses/test_generation.prompt.txt`, `test_generation.txt`
- `outputs/output.csv`, `outputs/exceptions.csv`
- `reports/system_validation_report.json`
