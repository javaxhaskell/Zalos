# Expense Exception Consistency Diagnosis: ce21e7e5-ce6f-4332-a645-d17075a4866d

## Summary

Session `ce21e7e5-ce6f-4332-a645-d17075a4866d` failed deterministic validation with:

`Exception list consistency: exception count 5 != flagged rows 0`

Generated pytest passed 7/7. Agent execution succeeded. The generated outputs are internally consistent; the validator used boolean-style truthiness instead of contract-backed enum semantics for `exception_flag`.

## Validation Failure

- **Check:** Exception list consistency (row_level)
- **Evidence:** `exception count 5 != flagged rows 0`
- **Terminal status:** `author_validation_failed`

## Evidence Table

### A. Contract Evidence

| Question | Answer |
| --- | --- |
| Workflow type | `expense_exception_review` |
| Required output files | `outputs/output.csv`, `outputs/exceptions.csv`, `reports/validation_report.md` |
| Exception output files | `outputs/exceptions.csv` — "Rows flagged for review containing only lines where exception_flag == 'exception'" |
| Required row-level columns | `expense_id`, `exception_flag`, `exception_reason`, `severity`, `rule_used`, `confidence` |
| Exception/review indicator column | `exception_flag` |
| `allowed_enums.exception_flag` | `["exception", "no_issue"]` |
| `exception_rules` | 4 rules (`amount_over_policy`, `missing_receipt`, `approval_not_approved`, `suspicious_notes`) |
| `output_column_semantics.exception_flag` | `producer_kind: exception_flag`; row semantics require `'exception'` when rules match, otherwise `'no_issue'` |
| Does contract imply REVIEW means flagged? | **No.** Contract uses `exception` / `no_issue`, not `REVIEW`. |

### B. Output Evidence

| Question | Answer |
| --- | --- |
| Rows in `outputs/output.csv` | 7 |
| Rows in `outputs/exceptions.csv` | 5 |
| Unique `exception_flag` values in output | `exception` (5 rows), `no_issue` (2 rows) |
| Exception file row IDs | EXP-001, EXP-002, EXP-004, EXP-005, EXP-007 |
| PK correspondence | All 5 exception rows match output rows with `exception_flag=exception` by `expense_id` |
| Missing from exceptions.csv | None — EXP-003 and EXP-006 correctly excluded (`no_issue`) |
| Extra in exceptions.csv | None |

### C. Validator Evidence

| Question | Answer |
| --- | --- |
| Function | `_layer_exception_consistency()` in `author_contract_validation.py` |
| Flag column selected | `exception_flag` (correct) |
| Truthiness function | `_truthy()` — accepts only `yes`, `true`, `1`, `y`, `flagged` |
| Value in output rows | `exception` — **not** in `_truthy()` set |
| Used `allowed_enums`? | **No** |
| Used `summary_metrics` filters? | **No** |
| Used `output_column_semantics`? | **No** |
| Agent output wrong? | **No** — agent writes `exception_flag == 'exception'` consistently |

### D. Root-Cause Decision

**Primary: validation-layer semantics too narrow (Option A)**

The contract explicitly defines `exception_flag` enum values `exception` / `no_issue`, summary metrics filter on `exception_flag == 'exception'`, and the exception file description requires `exception_flag == 'exception'`. The generated agent followed this. The validator counted flagged rows with `_truthy()`, which does not treat `exception` as flagged, yielding 0 flagged rows vs 5 exception rows.

## Rejected Alternative Explanations

| Alternative | Why rejected |
| --- | --- |
| Generated agent used wrong flag values | Output uses contract-correct `exception` / `no_issue`; exceptions.csv matches |
| `REVIEW` not truthy (prior hypothesis) | Output never contains `REVIEW`; values are `exception` / `no_issue` |
| Contract semantics unclear | Contract is explicit via enums, semantics, metrics, and exception file description |
| Wrong indicator column | Validator correctly selected `exception_flag` |
| Primary-key mismatch | PK sets match; failure is count-only due to truthiness |
| Exception file generation wrong | Agent filters `exception_flag == 'exception'`; file contents are correct |

## Proposed Fix

Make exception consistency validation contract-aware:

1. Infer flagged enum values from `summary_metrics` filters (e.g. `exception_flag == 'exception'`).
2. Otherwise derive flagged values from `allowed_enums` minus contract-declared non-flagged values (fallback semantics, common hints like `no_issue`).
3. Fall back to `_truthy()` only when contract semantics are unavailable (boolean-style flags).
4. Compare primary keys when available, not just counts.

## Risks

- Must not treat every non-empty enum as flagged; ambiguous multi-value enums without semantics continue using `_truthy()` fallback.
- Boolean-style `yes`/`no` contracts must keep working via fallback and binary enum inference.
- Non-flagged inference uses `fallback_value_semantics` only (not `row_semantics` examples) to avoid treating illustrative rule names as non-flagged values.

## Session Artifacts Inspected

- `.workspaces-deepseek-rerun/ce21e7e5-ce6f-4332-a645-d17075a4866d/events.jsonl`
- `generated/author_output_contract.json`
- `generated/agent.py`
- `generated/tests/test_agent.py`
- `outputs/output.csv`
- `outputs/exceptions.csv`
- `reports/system_validation_report.json`
- `reports/validation_report.md`
