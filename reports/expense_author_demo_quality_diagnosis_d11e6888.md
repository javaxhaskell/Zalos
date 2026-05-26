# Expense Exception Review Author Demo — Quality Diagnosis

**Session:** `d11e6888-886f-4284-8670-522c9a86bbf6`  
**Workspace:** `.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6`  
**Archive (evidence only, not patched):** `/Users/arhamshuaib/Downloads/agentforge-session-d11e6888-886f-4284-8670-522c9a86bbf6.zip`

**Run summary:** Author `expense_exception_review` completed honestly — 7 input rows, 5 flagged, generated pytest 6/6, four-tier system validation **PASS**, model stages `contract_planning`, `contract_schema_repair`, `contract_review`, `code_generation`, `test_generation`, 65,597 tokens.

---

## Issue matrix

| # | Area | Evidence (session archive) | Root cause class | General fix needed? | Fix target | Status |
|---|------|------------------------------|------------------|---------------------|------------|--------|
| 1 | Path leakage in `reports/validation_report.md` | Lines 5, 43: `/Users/arhamshuaib/Desktop/Zalos/.workspaces/d11e6888-.../uploads/input.csv` and absolute `outputs/output.csv` | Report-generation / codegen guidance gap | Yes | `user_facing.py`, `author_llm_authoring.py` report guidance, post-run sanitisation in `author_custom_build.py` | **Fixed (future sessions)** |
| 2 | Confidence vs severity | `generated/agent.py` L116–129: penalises confidence for high/medium severity and `num_rules > 1`; EXP-007 → 0.50 with four clear rules | Codegen guidance gap (model-authored agent) | Yes | `_exception_review_codegen_section()` in `author_llm_authoring.py` | **Fixed (future codegen)** |
| 3 | UI “All rows” vs 5 rows | Committed UI: `viewMode="preview"` + `LowConfidenceRows` (max 5) using `rowNeedsReview` (flagged **or** low confidence); main table label “Rows”, not all 7 | User-facing polish / misleading preview | Yes | `output-preview-card.tsx`, `data-preview-table.tsx` | **Fixed** |
| 4 | Category breakdown scope | Breakdown used all 7 rows in data, but paired with misleading low-confidence panel; user perceived flagged-only counts (Travel 3, Meals 1, Software 1) | User-facing polish | Yes | Scoped breakdown labels + flagged/all tabs | **Fixed** |
| 5 | Golden output skipped | `system_validation_report.md` Check 8 SKIPPED — no independent `expected_output.csv` | Honest validation (acceptable) | Optional only | Staging independent golden | **Documented — not implemented** |
| 6 | `system_validation_report.json` missing `validation_tier` | JSON checks only had `layer`; markdown had Tier lines | Validation/reporting quality gap | Yes | `validation/reporter.py` `render_json()` | **Fixed** |
| 7 | Generated test comment wording | `generated/tests/test_agent.py` L15–16: awkward “required_artifacts … not persisted” | Generated-test guidance bug | Yes | `_test_generation_prompt()` | **Fixed (future testgen)** |
| 8 | Token usage (65,597) | `contract_planning` 18,598; `contract_schema_repair` 12,740; `contract_review` 13,858; `code_generation` 9,535; `test_generation` 10,866 | Accepted audit-heavy cost | No broad optimisation this pass | — | **Documented limitation** |

---

## Per-issue evidence

### 1 — Absolute local path leakage

```5:5:.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/reports/validation_report.md
- Input file: /Users/arhamshuaib/Desktop/Zalos/.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/uploads/input.csv
```

```43:43:.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/reports/validation_report.md
- Row-level output written to: /Users/arhamshuaib/Desktop/Zalos/.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/outputs/output.csv
```

Internal `events.jsonl` may retain absolute paths; user-facing workflow reports should not.

### 2 — Confidence semantics

```116:129:.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/generated/agent.py
    # Confidence derivation
    if not flag:
        confidence = '0.95'
    else:
        num_rules = len(rules_used)
        penalty = 0.0
        if 'high' in severities:
            penalty += 0.2
        if 'medium' in severities:
            penalty += 0.1
        if num_rules > 1:
            penalty += 0.05 * (num_rules - 1)
        conf = max(0.5, 0.95 - penalty)
```

EXP-007 output row: `confidence=0.50` with four deterministic rules — conflates severity/rule-count with certainty.

### 3 — UI “All rows” label

Committed `output-preview-card.tsx` used `viewMode="preview"` (`maxRows=10`, section “Rows”) plus `LowConfidenceRows` capped at 5 rows where `rowNeedsReview` is true (all 5 flagged expense rows qualify). Users reasonably read the visible 5-row block as the primary “all rows” table.

### 4 — Category breakdown

Full `outputs/output.csv` category counts: Travel 3, Meals 2, Software 1, Office 1 (7 rows). Flagged-only: Travel 3, Meals 1, Software 1 (5 rows). Breakdown component always computed from full CSV; confusion came from adjacent scoped panels without explicit scope labels.

### 5 — Golden output

System report honestly skipped golden comparison. No independent golden was staged; codegen did not read golden files. Safe to skip for this pass.

### 6 — JSON validation tier

Pre-fix `system_validation_report.json` checks lacked `validation_tier`; events payload already included it (`author_custom_build.py` validation_run).

### 7 — Test wording

Archived `generated/tests/test_agent.py` header referenced non-persisted `required_artifacts` key — accurate internally but not audit-polished.

### 8 — Token usage

65,597 / 150,000 total. Contract planning + schema repair dominate (~48%). Acceptable for demo audit trail; not optimised in this pass.

---

## General fixes applied (code paths)

| Fix | Files |
|-----|-------|
| Post-run workflow report path sanitisation | `user_facing.py`, `author_custom_build.py` |
| Codegen/report relative-path guidance | `author_llm_authoring.py` `_report_section_requirements_section` |
| Exception confidence semantics guidance | `author_llm_authoring.py` `_exception_review_codegen_section` |
| `validation_tier` in JSON sidecar | `validation/reporter.py` |
| Test-generation wording | `author_llm_authoring.py` `_test_generation_prompt` |
| UI flagged/all tabs + scoped labels | `output-preview-card.tsx`, `data-preview-table.tsx`, `category-breakdown.tsx`, `finance-summary-card.tsx` |

**Not changed:** Archived session files, validation gates, Author path, row-count hardcoding, golden shortcut.

---

## Validation commands (TASK 4)

```bash
python -m compileall apps/api/src apps/api/tests -q
cd apps/api && python -m pytest tests/test_author_validation_architecture.py -q
cd apps/api && python -m pytest tests/test_author_codegen_reliability.py -q
cd apps/api && python -m pytest tests/test_validation_layers.py -q
cd apps/api && python -m pytest tests/test_generated_pytest_gate.py -q
cd apps/api && python -m pytest tests/test_final_demo_output_quality.py -q
cd apps/web && pnpm run typecheck
```

---

## Presentation readiness

| Criterion | Session d11e6888 | After general fixes (new runs) |
|-----------|------------------|--------------------------------|
| Author path honest | Yes | Yes |
| Deterministic validation PASS | Yes | Yes |
| Generated pytest | 6/6 | Expected same |
| User-facing paths clean | No | Yes (sanitise + guidance) |
| UI labels match data scope | Partially misleading | Yes (tabs + scoped labels) |
| JSON tier metadata | Incomplete | Complete |
| Golden comparison | SKIPPED (honest) | SKIPPED unless independent golden staged |
| Confidence semantics in output | Weak on multi-rule rows | Improved via codegen guidance only |

**Verdict:** Session `d11e6888` is a valid **completed** Author evidence run. Presentation polish issues are **archived-session artifacts**; future runs inherit general fixes without rewriting history.
