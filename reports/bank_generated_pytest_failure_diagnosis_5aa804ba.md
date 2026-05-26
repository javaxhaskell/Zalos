# Bank Generated Pytest Failure Diagnosis: 5aa804ba-0272-4c65-84fd-cfc94c56bfdc

## Summary

Session `5aa804ba-0272-4c65-84fd-cfc94c56bfdc` reached model-authored contract planning, contract review, code generation, test generation, agent execution, deterministic validation, and generated pytest. Agent execution exited 0 and produced both required artifacts. Deterministic validation passed schema, required columns, row-level, and enum checks. Generated pytest failed 2/8 tests. Execution repair ran twice but targeted `generated/tests/test_agent.py` only; both repair candidates failed all eight tests.

**Primary root cause: 7 — Mixed issue**

1. Generated agent misclassified three obvious demo rows because keyword rules used overly literal phrases (`bank service charge` instead of substring `service charge`; missing `airways` and `client receipt`).
2. Generated pytest misinterpreted the contract’s `clear_rule_confidence_08` formula by treating any non-empty `rule_matched` (including `"No rule matched"`) as a clear keyword match requiring confidence ≥ 0.80.
3. Pytest failure routing sent both failures to test-only repair (`failure_kind=pytest`) instead of agent repair for the classification/report failure.

Near-token budget (113,726 / 150,000) did not truncate model responses; repair prompts were not obviously truncated.

---

## Session Status

| Field | Value |
| --- | --- |
| Session ID | `5aa804ba-0272-4c65-84fd-cfc94c56bfdc` |
| Workflow | Bank Transaction Categorisation (`model_authored_finance_workflow`, `llm_custom`) |
| Template hint | `bank_categoriser` |
| Input | `uploads/bank_transaction_categorisation_demo.csv` (18 rows) |
| Terminal status | `failed_other` / `author_generated_code_failed` |
| Failed layer | `generated_pytest` |
| Budget | 113,726 / 150,000 tokens |

---

## A. Generated Pytest Failure Details

### Exact failing tests

1. `generated/tests/test_agent.py::test_report_contains_summary_evidence`
2. `generated/tests/test_agent.py::test_confidence_score_constraints`

### Exact assertion messages (reproduced locally)

**test_report_contains_summary_evidence**

```text
AssertionError: Category 'Bank Fees' missing from report
assert 'Bank Fees' in '# Validation Report\n\n...'
```

**test_confidence_score_constraints**

```text
AssertionError: Row 6: rule_matched='No rule matched' but confidence 0.4 < 0.80
assert 0.4 >= 0.8
```

### Interpretation

| Test | Output wrong? | Test expectation wrong? |
| --- | --- | --- |
| `test_report_contains_summary_evidence` | **Yes** — agent misclassified BTX-0007 (`BARCLAYS SERVICE CHARGE`) as `Other`, so `Bank Fees` never appears in report category tables | Partly — test requires every allowed enum category in report text even when zero rows use that category; primary failure is agent misclassification |
| `test_confidence_score_constraints` | **No** — agent correctly set `Other` rows to confidence 0.40 with `rule_matched='No rule matched'` per contract semantics | **Yes** — test treats any non-empty `rule_matched` as a clear rule match; contract formula is `(rule_matched == '' or rule_matched is null or confidence_score >= 0.80)` |

---

## B. Generated Agent Behavior

### Classification helper

- Builds lowercase haystack from `description` and `counterparty` only (reference omitted).
- Uses keyword lists with overly literal tokens, e.g. `bank service charge` instead of substring `service charge`.
- Fallback branch sets `category=Other`, `rule_matched='No rule matched'`, `confidence_score='0.40'`, `review_required='true'`.

### Misclassified rows (expected vs actual)

| transaction_id | Description | Expected | Actual | Why |
| --- | --- | --- | --- | --- |
| BTX-0007 | BARCLAYS SERVICE CHARGE | Bank Fees | Other | Keyword `bank service charge` does not match haystack containing `barclays service charge` |
| BTX-0010 | BRITISH AIRWAYS LHR-MAD | Travel | Other | Missing `airways` keyword |
| BTX-0017 | CLIENT RECEIPT INV-8840 | Revenue | Other | Missing `client receipt` keyword |

### Report generation

- `write_report()` lists only categories present in output rows.
- Because BTX-0007 was `Other`, `Bank Fees` is absent from Category Distribution and Category Summary Metrics tables.
- Report otherwise includes summary metrics (`transaction_count`, `total_debits`, `total_credits`, `net_amount`) for categories that appear in output.

### Confidence / review semantics

- Matched rows: confidence 0.85–0.95, `review_required=false` — correct.
- Other rows: confidence 0.40, `review_required=true` — correct per contract.

---

## C. Contract / Test Alignment

### Contract requirements (excerpt)

- `requested_deliverables`: validation report with category summary totals and review-flag evidence.
- `summary_metrics`: `transaction_count`, `total_debits`, `total_credits`, `net_amount` grouped by `category`.
- `validation_checks.clear_rule_confidence_08` formula: `(rule_matched == '' or rule_matched is null or confidence_score >= 0.80)`.
- `output_column_semantics.rule_matched.fallback_value_semantics`: empty string **or** note indicating no matching rule.
- `output_column_semantics.confidence_score`: ≥ 0.80 when clear keyword rule matched; < 0.70 when category is `Other`.

### Generated test alignment

| Test assertion | Contract-backed? |
| --- | --- |
| All nine allowed categories must appear in report text | **Overstrict** — contract requires summary evidence for categories present in output, not every enum label with zero rows |
| `if rule_matched.strip(): assert conf >= 0.80` | **Invalid** — contradicts `clear_rule_confidence_08` when fallback uses non-empty sentinel like `"No rule matched"` |
| Other confidence < 0.70 | Valid |
| review_required true for Other | Valid |

Codegen prompt included user-description keywords (`bank service charges`) but **not** bundled substring keyword guidance because `author_enable_bank_reference_scaffold` was off despite demo-shaped contract.

---

## D. Repair Attempts

| Attempt | Target | Result |
| --- | --- | --- |
| 1 | `generated/repairs/attempt_1/tests/test_agent.py` | 8/8 failed — repair rewrote tests, not agent |
| 2 | `generated/repairs/attempt_2/tests/test_agent.py` | 8/8 failed — same pattern |

Repair context included failing test names and assertion messages but `failure_kind=pytest` restricted repair to test file only. Repair did not receive bundled-bank keyword repair requirements because scaffold mode was disabled. Neither attempt modified `generated/agent.py`.

---

## E. Budget / Latency

- Tokens used: 113,726 / 150,000 (~76%) — elevated but not exhausted.
- `codegen_prompt_meta.json`: `max_tokens_set: false` (DeepSeek path).
- No evidence of truncated model responses in stored artifacts.
- Budget likely contributed to stopping after two repair attempts, but primary failure was mis-routed repair + prompt gaps, not truncation.

---

## Artifact Paths Inspected

- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/events.jsonl`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/manifest.json`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/author_output_contract.json`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/agent.py`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/tests/test_agent.py`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/model_responses/code_generation.prompt.txt`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/model_responses/test_generation.txt`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/repairs/attempt_1/tests/test_agent.py`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/repairs/attempt_2/tests/test_agent.py`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/outputs/output.csv`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/reports/validation_report.md`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/reports/system_validation_report.json`

Archive zip at `/Users/arhamshuaib/Downloads/agentforge-session-5aa804ba-0272-4c65-84fd-cfc94c56bfdc.zip` was not required; workspace artifacts were complete.

---

## Output Quality Assessment

| Artifact | Structurally valid? | Semantically acceptable? |
| --- | --- | --- |
| `outputs/output.csv` | Yes — 18 rows, all required columns non-null | **No** — 3/18 rows misclassified; 4 `Other` rows (expected 1 for demo) |
| `reports/validation_report.md` | Yes — non-empty, includes summary tables | **Partial** — missing `Bank Fees` because of misclassification |

---

## Proposed Narrow General Fix

1. **Codegen**: When contract matches demo-shaped bank categoriser (`_is_bundled_bank_reference_contract`) but scaffold flag is off, still inject substring keyword + haystack guidance (without hardcoding session outputs).
2. **Test generation**: Align `clear_rule_confidence_08` assertions with contract formula; do not treat fallback sentinels as clear matches. Assert report summary from output-derived categories, not all allowed enums.
3. **Pytest repair routing**: Route classification/report assertion failures to agent repair (`contract_validation`); reserve test-only repair for demonstrably unsupported test logic (e.g. `No rule matched` confidence misinterpretation).
4. **Execution repair prompts**: Include output/report excerpts and forbid weakening valid contract-backed tests.

---

## Risks

- Demo-shaped keyword guidance must not become deterministic post-processing of categories in backend execution paths — prompt guidance only.
- Expanding agent-repair routing must not skip legitimate test-only repairs (pathing, unsupported heuristics).
- Report assertions must stay contract-backed while avoiding brittle “every enum label in markdown” checks.
