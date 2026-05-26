# Author Validation / Testing Architecture Audit

**Date:** 2026-05-25  
**Scope:** AgentForge Author workflow — four-layer validation model  
**Thesis:** The LLM authors the workflow-specific contract and workflow-specific tests; the backend provides preset universal validators and contract-driven validators; golden-output comparison is optional independent evidence.

---

## Four-layer summary table

| Layer | Current implementation | Evidence | Gaps / ambiguity | Fix needed |
|---|---|---|---|---|
| **Universal checks** | Partially implicit in `validate_against_contract()` and `author_custom_build._enforce_final_author_artifacts()`. Explicit checks: required deliverables present (`_layer_required_deliverables`), row-level invariants via `layer_row_level` (row count + PK uniqueness), static safety scan before execution (`_safety_issues_for_files`), final artifact gate (agent.py, tests, archive, manifest, events). | `author_contract_validation.py` L43–62; `author_custom_build.py` L2174–2203, L1054–1104; `author_llm_authoring.py` L7584–8677 | Not grouped as “universal” in reports; mixed numbering with contract checks; bank-specific `layer_bank_categoriser_semantics` lives in `validation/layers.py` (demo path only, not custom Author). Row-level PK check is contract-keyed but structurally universal. | Group universal checks in `system_validation_report.md`; add `universal_validation_failed` error code; document universal vs contract-derived row checks. |
| **Contract-specific checks** | `validate_against_contract()` drives schema, required columns, calculated fields, allowed enums, summary group coverage, exception consistency from `AuthorOutputContract`. | `author_contract_validation.py` L44–101; expense fixture `expense_exception_consistency_ce21e7e5.json` | Checks interleaved with universal checks in flat numbered report layers; failure always `author_validation_failed` regardless of failing check type. Exception flag semantics recently improved but remain contract-inferred (`_contract_flagged_values`). | Separate contract-specific section in report; `contract_specific_validation_failed` error code; keep contract-driven semantics, no domain hardcoding. |
| **Workflow-specific generated tests** | Model generates `generated/tests/test_agent.py` via `_test_generation_prompt()`; backend runs pytest and gates via `layer_generated_pytest` / `generated_pytest_gate_failed` (min 3 collected, all pass). | `author_llm_authoring.py` L3048–3176; `validation/layers.py` L566–638; `author_custom_build.py` L2123–2171 | Prompt already strong but lacks explicit three-tier test structure (universal + contract + workflow-specific). Pytest failure uses generic `author_generated_code_failed`. Repair path conflates pytest vs contract failures in some messages. | Update test-generation prompt with explicit layer guidance; use `generated_pytest_failed` error code. |
| **Golden-output comparison** | `layer_golden_output()` — skipped unless `golden_comparison_requirement == "required"` and file staged. Bundled bank reference stages `evals/golden_output.csv` via `stage_bundled_bank_reference_golden()`. Codegen prompt excludes golden paths (`test_reference_sample_honesty.py`). | `author_contract_validation.py` L103–116; `validation/layers.py` L270–316; `author_llm_authoring.py` L465–488; `test_reference_sample_honesty.py` L177–191 | Golden skipped silently in most custom workflows (correct). Report labels layer but does not explain “optional independent oracle”. No distinct `golden_output_comparison_failed` code. | Add golden-oracle wording to report + docs; `golden_output_comparison_failed` when golden required and fails; reinforce codegen/test prompts. |

---

## Answers to the 12 audit questions

### 1. Which checks always run for every Author workflow?

For **custom LLM-authored workflows** (`validate_against_contract` path):

- Required deliverables present (all `contract.all_required_output_paths()`)
- Output schema (contract `output_columns`)
- Required columns non-null (contract `required_output_columns`)
- Row-level invariants (row count when input present; PK uniqueness when `primary_row_key` set)
- Calculated field formulas (when declared with non-empty formula)
- Allowed enum values (when `allowed_enums` non-empty)
- Summary group coverage (per `aggregation_specs`)
- Exception list consistency (per `exception_output_files`)
- Generated pytest gate (always attempted when `generated/tests` exists)
- Golden output (only when `golden_comparison_requirement == "required"`)

Additionally, outside the validation report: static safety scan, artifact presence gate, archive/manifest/events enforcement.

For **template-seeded bank demo** (`validate_output` tool path): six ADR-0007 layers plus optional `template_reference` and `semantic_rules` (bank-hardcoded — demo only).

### 2. Which checks are driven by AuthorOutputContract?

All checks in `validate_against_contract()` except the structural row-count/PK mechanics are contract-field-driven:

- `output_columns`, `required_output_columns`, `primary_row_key`, `preserve_row_count`
- `allowed_enums`, `calculated_fields`, `tolerances`
- `aggregation_specs`, `exception_output_files`, `exception_rules`
- `requested_deliverables` / deliverable paths
- `golden_comparison_requirement`, `golden_output_path`

### 3. Which checks are model-authored in generated/tests/test_agent.py?

Whatever the model writes in `generated/tests/test_agent.py` — typically:

- Agent CLI invocation smoke tests
- Required artifact existence/readability
- Contract-backed column and non-null assertions
- Optional workflow-specific behavioural tests (e.g. sample row category, exception flagging) when supported by prompt/contract/sample

Backend enforces collection (≥3 tests) and pass rate; does not prescribe test bodies.

### 4. Which checks are optional golden-output comparisons?

`layer_golden_output` when:

- `golden_comparison_requirement != "required"`, OR
- no staged golden CSV at `contract.golden_output_path`

Bundled bank reference sample sets `golden_comparison_requirement: required` and stages `evals/golden_output.csv`. Custom workflows default to skipped.

### 5. Are these layers clearly separated in code?

**Partially.** `validate_against_contract()` runs a flat list; `ValidationLayer` enum tags each check but there is no `ValidationTier` grouping. Template demo adds bank-specific `semantic_rules` outside the four-layer model. **Fix:** add tier classification module and grouped report rendering.

### 6. Are these layers clearly explained in reports/docs?

**No.** `system_validation_report.md` uses sequential “Layer N: {check name}” without universal/contract/pytest/golden grouping. UI (`validation-summary-card.tsx`) maps individual check names to finance language but not the four-tier model. No `docs/AUTHOR_VALIDATION_MODEL.md` yet.

### 7. Are generated tests using actual contract fields?

**Mostly yes.** Prompt passes `_compact_codegen_contract`, `_required_artifact_specs`, `row_level_output_file`, deliverable paths. Known gap: prompt references `required_artifacts` list (assembled from contract) while persisted `author_output_contract.json` omits that key — prompt explicitly warns tests not to read `required_artifacts` from JSON.

### 8. Are generated tests allowed to be workflow-specific?

**Yes, conditionally.** Prompt allows contract-backed behavioural tests when `validation_checks` or explicit rules exist; bundled/custom bank paths add extra requirements. Prompt also says “do not invent domain heuristics.” Workflow-specific tests are allowed when traceable to prompt/contract/sample.

### 9. Are generated tests protected against overstrict unsupported assertions?

**Partially.** Prompt forbids exact report wording unless required, snake_case metric literals, inventing domain rules, and asserting every enum label. No automated lint of generated tests beyond pytest execution.

### 10. Are golden outputs prevented from becoming codegen shortcuts?

**Yes for codegen.** `test_reference_sample_honesty.test_golden_output_is_not_codegen_shortcut` asserts golden paths absent from `_codegen_prompt()`. Golden staged only post-scaffold for validation. **Gap:** test-generation prompt does not explicitly forbid reading golden as implementation oracle (only codegen prompt tested).

### 11. Does the system report which layer failed when a run fails?

**Partially.** `CompletionFailureMetadata` records `failed_check`, `failed_layer`, and `validation_failures`. Error codes collapse to `author_validation_failed` or `author_generated_code_failed`. Event payload includes per-check `layer_results` but not validation tier.

### 12. Does the UI communicate this in finance-user language?

**Partially.** `ValidationSummaryCard` translates check names (e.g. “Exception list consistency”, “Independent golden comparison”, “Generated pytest suite”) but does not group into the four architectural layers. No tier headings in UI.

---

## Files inspected

| File | Role |
|---|---|
| `author_contract_validation.py` | Contract-driven validation orchestration |
| `author_llm_authoring.py` | Codegen/test prompts, golden staging, safety |
| `author_custom_build.py` | Pipeline, pytest gate, failure finalization |
| `validation/layers.py` | Layer implementations (+ bank semantic hardcoding) |
| `validation/reporter.py` | Markdown report rendering |
| `tools/validation_tools.py` | Template-path six-layer tool |
| `schemas/validation.py` | ValidationLayer enum |
| `schemas/author_output_contract.py` | Contract schema |
| Fixtures: `bank_generated_pytest_failure_5aa804ba.json`, `expense_generated_pytest_pathing_failure_fafd3422.json`, `expense_exception_consistency_ce21e7e5.json`, `expense_generated_agent_event_log_corruption_3bb42792.json` | Regression evidence |
| Tests: `test_validation_layers.py`, `test_generated_pytest_gate.py`, `test_author_codegen_reliability.py`, `test_author_custom_workflow_gate.py`, `test_author_artifact_report_separation.py`, `test_reference_sample_honesty.py`, `test_author_token_optimization.py` | Existing coverage |

---

## Recommended fixes (implemented in TASK 2)

1. Add `validation_architecture.py` with tier classification and failure error-code mapping.
2. Group `system_validation_report.md` into four sections with suggested wording.
3. Add layer-specific `ErrorCode` values and use them in `author_custom_build.py`.
4. Strengthen test-generation and codegen prompts (workflow-specific OK; golden oracle-only).
5. Add `docs/AUTHOR_VALIDATION_MODEL.md`.
6. Add `test_author_validation_architecture.py` regression tests.

---

## Remaining limitations (post-fix)

- Bank demo `layer_bank_categoriser_semantics` remains hardcoded for template path; not used in custom Author validation.
- UI still lists checks individually rather than under four tier headings (backend report is the primary fix).
- Generated test quality depends on model adherence; backend only gates collection count and pass/fail.
- Exception flag semantics require contract hints (`allowed_enums`, `summary_metrics`, `output_column_semantics`); ambiguous contracts may still misclassify flagged rows.
- No claim of universal correctness — evidence-based validation only.
