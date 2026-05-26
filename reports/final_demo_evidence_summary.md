# Final Demo Evidence Summary — System Walkthrough

**Generated:** 2026-05-26 (docs sync)  
**Scope:** Expense Exception Review (Author) + Invoice Aging Cleanup v2 (Repair)  
**Canonical evidence:** Author **GREEN** · Repair **GREEN**

---

## Summary table

| Flow | Workflow | Session ID | Status | Runtime | Tokens | Pytest | Validation | Verdict |
|------|----------|------------|--------|--------:|-------:|--------|------------|---------|
| **Author** | Expense Exception Review | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` | `completed` | **190.6 s** | **44,964** | **5/5 pass** | Four-tier **PASS** (golden **7/7 PASS**) | **GREEN** |
| **Repair** | Invoice Aging v2 boundary | `b8317f51-384a-469a-92d3-90443853a4c5` | `completed` | **~10.6 s** | ~4,956 | **5/2 → 7/0** | Repair report + patch validated | **GREEN** |

**Author archive:** `.workspaces/98942274-b4fb-4e0d-a3b8-0b8144bc01db/archive.zip`  
**Repair archive:** `.workspaces/b8317f51-384a-469a-92d3-90443853a4c5/archive.zip`

Detailed Author inspection: [`final_expense_author_green_evidence.md`](./final_expense_author_green_evidence.md)

---

## 1. Final Author evidence — GREEN

**Session:** `98942274-b4fb-4e0d-a3b8-0b8144bc01db`  
**URL:** <http://localhost:3000/author/98942274-b4fb-4e0d-a3b8-0b8144bc01db>

- **Prompt:** *Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.*
- **Input:** `blind_eval_cases/expense_exception_review/input.csv` (7 rows)
- **Path:** DeepSeek pro (planning/review) + flash (codegen/testgen); `build_mode: llm_custom`
- **Outputs:** `outputs/output.csv` (7 rows preserved), `outputs/exceptions.csv` (5 flagged rows)
- **Flagged rows:** EXP-001, EXP-002, EXP-004, EXP-005, EXP-007 (5/7 — oracle-aligned)
- **Model stages:** contract_planning → contract_review → code_generation → test_generation
- **Scaffold events:** 0
- **User-facing report paths:** clean (workspace-relative only)

### Four validation tiers

| Tier | Status |
|------|--------|
| Universal | PASS |
| Contract-specific | PASS |
| Generated pytest | PASS (5/5) |
| Golden-output | **PASS (7/7 rows matched)** |

**Overall:** PASS

**Architecture:** Model authors the output contract, agent code, and tests. Backend validators enforce all four tiers. Golden at `evals/golden/expense_exception_review/` is **validation-only** — not injected into codegen prompts.

**Superseded sessions (do not use as primary):** `d11e6888`, `4a9a4343` (AMBER — path leakage or incomplete golden).

---

## 2. Final Repair evidence — GREEN

**Session:** `b8317f51-384a-469a-92d3-90443853a4c5`  
**URL:** <http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5>

- Fixture: `invoice_aging_v2` — boundary bug at 31 days
- Before: **5 pass / 2 fail** · After: **7 pass / 0 fail**
- Patch: `<= 31` → `<= 30` in `working/agent.py`
- `completion_via: repair_validated_patch`

---

## 3. Product Behavior compliance

| Flow | Verdict | Notes |
|------|---------|-------|
| **Repair** | **GREEN** | All 9 repair behaviors met on `b8317f51`; ~10 s; clear 5/2→7/0 story |
| **Author** | **GREEN** | Completed session with four-tier PASS including golden 7/7; 5/7 flagged rows; clean paths; 0 scaffold |
| **Overall** | **GREEN** | Repair-first live demo + Author pre-opened walkthrough |

Historical audit (pre-green Author): [`product_behavior_demo_compliance_audit.md`](./product_behavior_demo_compliance_audit.md) — superseded notice at top.

---

## 4. Demo order and narration

1. **Repair** — Live or pre-opened: <http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5> (~10 s). Walk 5/2 → 7/0, patch diff, archive.
2. **Author** — Pre-opened completed: <http://localhost:3000/author/98942274-b4fb-4e0d-a3b8-0b8144bc01db> (~3 min walkthrough). Walk four-tier validation, golden PASS, exceptions CSV, output preview. Live run optional (~3 min with DeepSeek).

**Walkthrough source:** [`docs/LOCAL_WALKTHROUGH.md`](../docs/LOCAL_WALKTHROUGH.md)

---

## 5. Honest limitations

1. **Author runtime** — Live run ~3 min (DeepSeek); pre-open green session for live demo.
2. **Expense review queue** — UI triage only; decisions persist in sessionStorage and do **not** mutate output files.
3. **Reference samples** — Provide prompt context only; do not bypass LLM-first Author path.
4. **Retry** — Resume restores state; new attempt = full rerun.
5. **Repair advisory LLM** — One optional DeepSeek call; deterministic pipeline owns patch validation.
6. **Eval drift** — CI `R-01` targets `invoice_aging_v1`; live demo uses **v2**. CI `A-01` uses bank categoriser; live Author demo uses **expense**.

---

## 6. Report back

| Item | Value |
|------|-------|
| **Author session** | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` · `completed` · 190.6 s · **GREEN** |
| **Author archive** | `.workspaces/98942274-b4fb-4e0d-a3b8-0b8144bc01db/archive.zip` |
| **Repair session** | `b8317f51-384a-469a-92d3-90443853a4c5` · **GREEN** |
| **Summary path** | `reports/final_demo_evidence_summary.md` |
| **Ready for packaging** | **Yes** — both flows GREEN with honest limitations documented |
