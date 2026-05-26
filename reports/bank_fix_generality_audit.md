# Bank Pytest Fix & Token Optimization — Generality Audit

**Session under review:** `5aa804ba-0272-4c65-84fd-cfc94c56bfdc`  
**Scope:** Recent fixes for bank generated-pytest failures and token reduction (113k → ~45k).  
**Question:** Does production logic remain general, with bank specificity confined to gated demo/reference paths?

---

## Executive answers (7 questions)

### 1. Production branches checking bank / session / BTX- / vendor names?

**No session-ID branching.** `5aa804ba` appears only in fixtures, diagnosis reports, and test assertions — not in orchestrator runtime code.

**Bank branching uses structural gates, not vendor/session literals:**

| Gate | Conditions |
| --- | --- |
| `_is_bundled_bank_reference_sample` | `template_hint == "bank_categoriser"`, exact 9 demo columns, `row_count == 18`, input basename `bank_transaction_categorisation_demo.csv` |
| `_is_bundled_bank_reference_contract` | Same template + columns + demo filename on contract (no row-count check) |
| `_is_custom_bank_categoriser_sample` | Bank template + bank-transaction schema, excluding bundled sample |
| `_is_custom_bank_categoriser_contract` | Bank template + non-bundled contract with `allowed_enums.category` |

**BTX-* / Stripe:** Used only in `_BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS` and bundled-scaffold/repair/test prompts when scaffold mode is on. Not used as runtime `if transaction_id == ...` logic.

**Vendor:** One generic codegen line (“combine transaction description and counterparty…”) applies to all workflows; no vendor-name allowlist in production branches.

**Post-run validation:** `_bank_reference_sample_validation_checks` (18-row counts, BTX expectations) runs only when `bundled_bank_reference_mode` is enabled in `author_custom_build.py`.

### 2. What “demo-shaped” actually scopes to?

**“Demo-shaped” = bundled bank reference *contract shape* without scaffold mode.**

Triggered when `_is_bundled_bank_reference_contract(...)` is true **and** `enable_bundled_bank_reference_guidance` / `author_enable_bank_reference_scaffold` is **false**.

This is the path that fixed session `5aa804ba`: model-authored contract on the standard demo CSV/columns, scaffold flag off.

**In scope:** substring keyword codegen (`_demo_shaped_bank_keyword_codegen_section`), lighter test-gen addendum, `demo_shaped_bank_constraints` on agent repair.

**Out of scope:** per-row BTX expected outputs, exact category counts, golden-output policy, deterministic contract scaffold seeding.

Naming is bank-specific; behavior is “reference demo contract shape,” not “any small CSV.”

### 3. Codegen guidance — general vs bank keyword lists?

**General (all workflows):** CLI interface, required artifacts, report sections, row-completeness invariant, numeric/date coercion, safety scan rules, contract column-list shape, DictWriter guidance, deliverable discipline — all contract-driven.

**Bank-specific (gated):**

| Branch | When | Keyword content |
| --- | --- | --- |
| Bundled scaffold | Scaffold flag + bundled sample | Full section + BTX rows + exact counts |
| Demo-shaped | Bundled contract shape, scaffold off | Substring/haystack rules + shared keyword groups, **no** BTX rows |
| Custom bank | Bank template, non-demo upload | Keyword groups mapped to contract `allowed_enums` |

All three reuse `_BUNDLED_BANK_REFERENCE_KEYWORD_RULES` — a committed demo keyword table, injected into **prompts only** (no backend reclassification).

### 4. Test-gen guidance — general vs bank special-case?

**General:** `_classification_test_generation_requirements()` applies to **any** contract with `clear_rule_confidence_08`, `confidence_score`, `rule_matched`, and/or `summary_metrics`. This fixes the `No rule matched` / report-enum overreach without bank coupling.

**Bank-specific add-ons (gated):**

| Mode | Extra requirements |
| --- | --- |
| Bundled scaffold | 18 rows, exact counts, all BTX row assertions |
| Demo-shaped | Row-count + prefer formula/report checks over full enum sweep |
| Custom bank | Row count from schema, no BTX/demo counts |

### 5. Repair routing — general vs bank special-case?

**Routing (`_pytest_failure_repair_kind` in `author_custom_build.py`) is general:**

- Agent repair (`contract_validation`) when required columns empty, classification/report signals (`Category '…' missing from report`, enum mismatch), or combined failures.
- Test-only repair (`pytest`) when `_pytest_signals_clear_rule_confidence_test_misinterpretation` matches and no agent signals.

This directly addresses the `5aa804ba` mis-route (both failures previously went to test-only repair).

**Bank-specific repair *context* (not routing):** repair prompts attach `reference_sample_constraints`, `demo_shaped_bank_constraints`, or `custom_bank_constraints` based on the same gates as codegen. Pytest repair still targets `test_agent.py`; agent repair targets `agent.py`.

### 6. Fixtures OK for bank examples?

**Yes.** Fixtures (`bank_generated_pytest_failure_5aa804ba.json`, bank demo execution/tuple/fieldnames fixtures) document regression cases with session IDs, assertion text, and snippets. Tests assert prompt *content* and routing behavior; they do not drive production logic.

Using bank demo data in fixtures/tests is appropriate for the product’s primary reference workflow.

### 7. Production prompt examples — generic vs bank rows?

| Prompt path | Generic examples | Bank/demo row literals |
| --- | --- | --- |
| General contract/codegen/test | Contract fields, CLI args, finance coercion | None |
| Demo-shaped codegen/test | Substring rules, formula semantics | Keyword groups only; **no BTX-* in demo-shaped test gen** |
| Bundled scaffold codegen/test/repair | Haystack sketch | **BTX-0001…BTX-0018**, exact counts, Stripe/barclays examples |
| Custom bank codegen/test/repair | Mapped category labels | Keyword groups; explicitly ** forbids** BTX/demo counts |

Production prompts for the `5aa804ba` fix path (demo-shaped, scaffold off) intentionally avoid hardcoding session outputs while still supplying substring keyword guidance.

---

## Evidence table

| Area | Evidence | General? | Demo-specific? | Risk | Recommendation |
| --- | --- | --- | --- | --- | --- |
| Detection gates | `_is_bundled_bank_reference_*`, `_is_custom_bank_categoriser_*` in `author_llm_authoring.py` | Yes — template + schema + filename | Bundled sample also requires 18 rows + demo filename | Low | Document gates in architect skill; optional rename `demo-shaped` → `reference_contract_shape` |
| Session / BTX runtime | Grep: no `5aa804ba` in `apps/api/src` | Yes | BTX only in constants + scaffold prompts | Low | None |
| Token optimization | `_is_small_author_workflow` row threshold; compact contract/repair; tests use bank profile as example only | Yes | Bank CSV used as test fixture, not hardcoded in logic | Low | Add one non-bank small-workflow test for parity |
| General codegen | `_codegen_prompt` contract/CLI/coercion/safety sections | Yes | No | Low | None |
| Demo-shaped codegen | `_demo_shaped_bank_keyword_codegen_section()` when bundled contract + scaffold off | Partial — bank keyword table | Yes — demo CSV/columns/filename | Medium | Extract keyword table to `reference_samples/bank_categoriser.py`; clarify name in prompts |
| Bundled scaffold codegen | `_bundled_bank_reference_codegen_section()` + BTX rows | No | Yes — full demo | Low (flag-gated) | Keep behind `author_enable_bank_reference_scaffold` |
| Custom bank codegen | `_custom_bank_categoriser_codegen_section()` | Partial — reusable patterns | Yes — bank template path | Medium | Narrow `_is_custom_bank_categoriser_contract` if non-bank templates reuse `bank_categoriser` hint |
| General test-gen | `_classification_test_generation_requirements()` | Yes | No | Low | None |
| Demo-shaped test-gen | Extra 2 lines + classification block; no BTX assertions | Partial | Yes | Low | None |
| Bundled test-gen | `_bundled_bank_reference_test_generation_requirements()` | No | Yes | Low (flag-gated) | None |
| Pytest repair routing | `_pytest_failure_repair_kind`, signal helpers | Yes — assertion/message heuristics | Uses bank-like category/report messages | Low | Consider generic phrasing: `classification label missing from report` |
| Repair prompt context | `demo_shaped_bank_constraints` injects keyword section lines | Partial | Yes | Low | Same module extraction as codegen |
| Post-run demo validation | `_bank_reference_sample_validation_checks` in `author_custom_build.py` | No | Yes — 18 rows, BTX | Low (flag-gated) | None |
| Compact pytest repair | `_compact_pytest_repair_requirements()` mentions `clear_rule_confidence_08` | Yes | Example text is bank-agnostic | Low | None |
| Tests | `test_author_codegen_reliability.py`, `test_author_custom_workflow_gate.py`, fixture JSON | N/A (tests) | Bank examples by design | Low | OK; session workspace path in one test is test-only |

---

## Verdict: **AMBER**

**Rationale:** Production remains **architecturally general** for non-bank workflows. Token optimizations and classification/test/repair fixes are **contract-driven and reusable**. However, **bank demo knowledge lives in production** (`_BUNDLED_BANK_REFERENCE_*` constants, “demo-shaped” naming, shared keyword table) and is activated by **product template + demo filename**, not merely by fixtures. That is intentional for the reference demo path but is not fully “generic AgentForge” — it is **scoped reference-sample logic embedded in the orchestrator**.

**Not RED because:** No session-specific branching, no backend deterministic reclassification, gates are explicit, and the `5aa804ba` fix path avoids BTX hardcoding in demo-shaped prompts. Fresh rerun (`1939f0a5`) completed at ~45k tokens with zero repairs.

---

## Smallest refactor (if pursuing GREEN later — no implementation here)

1. **Rename for clarity:** `demo-shaped` → `reference_bank_contract_shape` in function names, prompt headings, and event payload keys (behavior unchanged).
2. **Extract reference sample:** Move `_BUNDLED_BANK_REFERENCE_*` and keyword/row tables to `agentforge/reference_samples/bank_categoriser.py`; orchestrator imports gates + prompt sections only when `template_hint == bank_categoriser`.
3. **Tighten custom-bank gate:** Require `_is_bank_transaction_schema(schema_profile)` in `_is_custom_bank_categoriser_contract`, not merely `allowed_enums.category` + bank template.
4. **Test parity:** One `expense_exception_review` or `vendor_payment` small-workflow test asserting compact prompts and `_classification_test_generation_requirements` without bank template hint.

---

## Key findings for parent

- **Fixes are mostly general:** `clear_rule_confidence_08` test guidance, report category derivation, pytest→agent repair routing, and token compaction apply across workflows.
- **“Demo-shaped” is a deliberate third mode:** bundled contract shape + scaffold **off**; injects substring keywords but **not** BTX row literals — this is what closed the `5aa804ba` gap.
- **Bank literals stay scaffold-gated:** BTX rows, exact counts, and golden validation require `author_enable_bank_reference_scaffold`.
- **Residual coupling:** Shared `_BUNDLED_BANK_REFERENCE_KEYWORD_RULES` and `bank_categoriser` template hint centralize demo knowledge in production; acceptable for AMBER, refactor above would clarify boundaries.

**Related artifacts:** [bank_generated_pytest_failure_diagnosis_5aa804ba.md](./bank_generated_pytest_failure_diagnosis_5aa804ba.md), [token_usage_diagnosis_5aa804ba.md](./token_usage_diagnosis_5aa804ba.md)
