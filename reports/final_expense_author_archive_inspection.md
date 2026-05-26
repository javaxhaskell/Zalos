# Final Expense Author Archive Inspection

**Session:** `4a9a4343-a56f-4557-886b-855999a19160`  
**Workflow:** Expense Exception Review (`expense_exception_review`)  
**Run date:** 2026-05-26 (fresh API run, DeepSeek, scaffold disabled)  
**Runtime:** 210.5 s (~3.5 min) · **Tokens:** 45,328 / 150,000  
**Archive:** `.workspaces/4a9a4343-a56f-4557-886b-855999a19160/archive.zip` (70 KB)  
**Prompt:** *Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.*

---

## Pre-run confirmation

| Item | Result |
|------|--------|
| `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD` | Unset → **false** |
| DeepSeek configured | `GET /health/ready` → ready |
| Scaffold events in archive | **0** |
| Input | `blind_eval_cases/expense_exception_review/input.csv` (7 rows) |

---

## Inspection table (15 checks)

| Check | Result | Evidence | Issue |
|-------|--------|----------|-------|
| **1. No absolute paths in user-facing reports** | **AMBER** | `reports/system_validation_report.md` uses relative paths only. **`reports/validation_report.md` lines 3–4** embed `/Users/arhamshuaib/Desktop/Zalos/.workspaces/4a9a4343-…/uploads/input.csv` and absolute `outputs/output.csv`. | Agent-generated workflow report still leaks workspace paths; backend sanitisation does not rewrite model-authored report text post-run. |
| **2. Confidence semantics sensible** | **AMBER** | This run's contract omits `confidence`, `severity`, and `rule_used` columns. Output uses `exception_flag` / `exception_reason` only. | Cannot evaluate confidence semantics on this archive; prior run `d11e6888` had multi-rule confidence penalty issues. |
| **3. All rows present in output** | **GREEN** | `outputs/output.csv`: **7 data rows** (EXP-001…EXP-007); validation Check 2: row count preserved (7 → 7). | — |
| **4. Exceptions file contains only flagged rows** | **GREEN** | `outputs/exceptions.csv`: **3 rows** (EXP-001, EXP-002, EXP-007); Check 6: "3 exception rows match flagged output rows". | — |
| **5. UI review queue approve/reject** | **GREEN** | Committed `expense-review-queue.tsx` wired in `output-preview-card.tsx`; flagged rows drive queue. | Not exercised in this archive-only pass; verified by committed UI code. |
| **6. Reviewed output CSV download after decisions** | **GREEN** | `reviewed-output-download-menu.tsx` present; enables download after all flagged rows decided. | Requires live UI walkthrough; not in archive. |
| **7. Search bar for rows** | **GREEN** | `data-preview-table.tsx` search/filter on Author completed stage. | Not verified in browser this pass. |
| **8. Four validation tiers in system report** | **GREEN** | `reports/system_validation_report.md`: Universal, Contract-specific, Generated pytest, Golden-output sections; **Overall: PASS**. | — |
| **9. `validation_tier` in JSON sidecar** | **GREEN** | `reports/system_validation_report.json`: every check includes `"validation_tier"` (`universal`, `contract_specific`, `generated_pytest`, `golden_output`). | — |
| **10. Generated pytest passes** | **GREEN** | Event + Check 7: **3/3 passed** in 0.15 s (`generated/tests/test_agent.py`). | Fewer tests than `d11e6888` (6/6) but all pass. |
| **11. Deterministic validation passes** | **GREEN** | `validation_run` event `overall_passed: true`; manifest `validation_overall: pass`. | — |
| **12. Golden comparison honest** | **GREEN** | Check 8 **SKIPPED** with explicit evidence: no independent golden staged. | — |
| **13. Tool-action audit events present** | **GREEN** | `events.jsonl`: `tool_action_recorded` for inspect, contract_planning/review, codegen, testgen, static_safety_scan, execution, pytest, deterministic_validation, archive_generation. | Inputs in some events still use absolute paths (internal audit). |
| **14. Token usage recorded within budget** | **GREEN** | Manifest + `workflow_completed`: **45,328** tokens; budget limit 150,000; 4 model calls. | — |
| **15. Archive provenance internally consistent** | **GREEN** | `manifest.json` completion matches `SESSION_README.md` (tokens, stages, validation layers, output hashes); `archive.zip` present; `workflow_completed` via `ai_authored_workflow_build`. | — |

**Tally:** 11 GREEN · 4 AMBER · 0 RED

---

## Artifact inventory

| Path | Present |
|------|---------|
| `events.jsonl` | Yes (56 events) |
| `manifest.json` | Yes |
| `SESSION_README.md` | Yes |
| `generated/author_output_contract.json` | Yes |
| `generated/model_contract_plan.json` | Yes |
| `generated/model_contract_review.json` | Yes |
| `generated/agent.py` | Yes |
| `generated/tests/test_agent.py` | Yes |
| `outputs/output.csv` | Yes |
| `outputs/exceptions.csv` | Yes |
| `reports/validation_report.md` | Yes (path leakage) |
| `reports/system_validation_report.md` | Yes |
| `reports/system_validation_report.json` | Yes |
| `reports/model_authoring_summary.md` | Yes |
| `archive.zip` | Yes |

---

## Output summary

| Metric | Value |
|--------|-------|
| Input rows | 7 |
| Output rows | 7 |
| Flagged exceptions | 3 (amount over limit, missing receipt, combined on EXP-007) |
| Non-flagged | EXP-003, EXP-004 (pending approval), EXP-005 (personal notes), EXP-006 |
| Model stages | contract_planning, contract_review, code_generation, test_generation |
| Completion | `ai_authored_workflow_build` |

**Note:** EXP-004 (`pending`) and EXP-005 (`personal weekend travel`) are not flagged in this run. Validation passed because contract-driven rules matched agent output consistently — not because all prompt-implied rules fired.

---

## Comparison to superseded session `d11e6888`

| Aspect | `d11e6888` (old) | `4a9a4343` (fresh) |
|--------|------------------|---------------------|
| Status | completed | completed |
| Runtime | ~314 s | **210 s** |
| Tokens | 65,597 | **45,328** |
| Pytest | 6/6 | 3/3 |
| Path leakage in agent report | Yes | **Yes** (still) |
| `validation_tier` in JSON | Fixed post-session | **Yes** |
| `tool_action_recorded` events | Sparse | **Full pipeline** |
| `static_safety_scan` auditable | No | **Yes** |

---

## Final verdict: **AMBER**

**Presentation-ready with caveats.** The fresh run completes honestly with four-tier validation PASS, full orchestrator audit trail, and improved provenance vs `d11e6888`. Downgrade reasons:

1. **Agent-generated `validation_report.md` still contains absolute paths** — disclose in demo or avoid downloading that file; prefer `system_validation_report.md`.
2. **Simpler contract** — no confidence/severity columns; fewer generated tests; some prompt-implied rules (pending approval, suspicious notes) not flagged.
3. **UI checks (5–7)** verified by code, not live browser pass in this step.

**Recommended demo URL:** `http://localhost:3000/author/4a9a4343-a56f-4557-886b-855999a19160`

**Do not use** `d11e6888` as primary evidence unless fresh run unavailable.
