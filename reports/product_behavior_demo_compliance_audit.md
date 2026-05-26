# Product Behavior Demo Compliance Audit

> **Historical report.** Superseded by final evidence session `98942274-b4fb-4e0d-a3b8-0b8144bc01db` and [`final_demo_evidence_summary.md`](./final_demo_evidence_summary.md). Author verdict below is **AMBER** on session `d11e6888`; canonical Author evidence is **GREEN** on session `98942274`.

**Date:** 2026-05-25  
**Scope:** Take-home Product Behavior for **Author** (`expense_exception_review`) and **Repair** (`invoice_aging_v2`)  
**Method:** Read-only inspection of workspaces, reports, fixtures, and load-bearing code paths. No code changes, no commits, no LLM reruns.  
**Thesis (INV-1):** The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records.

**Grading rules (strict):**
- **GREEN** — Freshest session artifact demonstrates the requirement on the target demo path.
- **AMBER** — Code/tests suggest compliance but **fresh artifact missing**, **old archive only**, **partial pass**, or **known presentation gap** in the candidate session.
- **RED** — Requirement not met on the demo path even with code inspection.

**Requirement source:** Author 11-step pipeline and Repair 9-step pipeline in [`docs/ARCHITECTURE_AND_DESIGN.md`](../docs/ARCHITECTURE_AND_DESIGN.md), cross-checked against [`docs/SUBMISSION_CHECKLIST.md`](../docs/SUBMISSION_CHECKLIST.md) and backend gates (`enforce_author_completion_gate`, Repair evidence panel in `repair/[sid]/page.tsx`).

---

## Phase 1 — Candidate sessions and evidence inventory

### Freshest successful runs (primary)

| Demo | Session ID | Status | Runtime | Key artifact evidence | Archive |
|------|------------|--------|---------|----------------------|---------|
| **Author** `expense_exception_review` | `d11e6888-886f-4284-8670-522c9a86bbf6` | `completed` | **314 s (~5.2 min)** | 6/6 pytest; four-tier validation **PASS**; `completion_via=ai_authored_workflow_build`; 65,597 tokens; 0 scaffold events | `.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/archive.zip` (89 KB) |
| **Repair** `invoice_aging_v2` | `b8317f51-384a-469a-92d3-90443853a4c5` | `completed` | **10.6 s** | 5/2 → 7/0 pytest; patch `<=31` → `<=30`; `completion_via=repair_validated_patch` | `.workspaces/b8317f51-384a-469a-92d3-90443853a4c5/archive.zip` (20 KB) |

Recorded in: `reports/_fresh_run_author.json`, `reports/_fresh_run_repair.json`, `reports/final_fresh_demo_evidence.md` (addendum, commit `579fa30`).

### Supporting / historical sessions

| Session ID | Demo | Status | Role in audit |
|------------|------|--------|---------------|
| `72bf763b-fd65-470c-ae6d-7e0ebbeed771` | Repair v2 | `completed` | Earlier same-day success (~13 s); corroborates repair path |
| `da9b613a-7626-4f25-ba6b-7be7775547b6` | Author expense | `failed_other` | Pre-fix crash: `AttributeError` on post-pytest validation (`summary_metrics` str vs object) |
| `d931c367-dbec-436c-93d8-4ef94e77dfbd` | Author bank fallback | `failed_other` | Generated pytest 6/7; four-tier partial — not target demo |
| `ce21e7e5-ce6f-4332-a645-d17075a4866d` | Author expense | `failed_other` | `.workspaces-deepseek-rerun/` — exception consistency FAIL (5 exceptions vs 0 flagged rows) |
| `fafd3422-a717-465b-b656-cc382892771f` | Author expense | `failed_other` | `.workspaces-deepseek-benchmark/` — pytest 0/7; honest failure |

### Fixture baseline (Repair)

`fixtures/broken_agents/invoice_aging_v2/` — local pytest (api venv): **5 passed, 2 failed** (`test_boundary_31_days_in_31_60_bucket`, `test_output_matches_expected_output_csv`). Matches repair session before-fix evidence.

### Machine-readable summaries

- `reports/_fresh_run_author.json` — primary Author evidence pointer  
- `reports/_fresh_run_repair.json` — primary Repair evidence pointer  
- `reports/expense_author_demo_quality_diagnosis_d11e6888.md` — Author presentation-quality gaps on primary session  
- `reports/final_fresh_demo_evidence.md` — full fresh-run narrative (includes superseded failed runs)

### Archives note

No top-level `archives/` directory. Evidence lives under `.workspaces/<session-id>/` and `.workspaces-deepseek-rerun/`.

---

## Phase 2 — Author (`expense_exception_review`) compliance

**Primary evidence session:** `d11e6888-886f-4284-8670-522c9a86bbf6`  
**UI entry:** Dashboard → Author → template **Expense Exception Review** → sample `/api/reference-samples/expense-exception-review` (9-row reference CSV; fresh run used 7-row blind-eval input with equivalent prompt).

| # | Product behavior requirement | Status | Evidence | Gap | Suggested fix |
|---|------------------------------|--------|----------|-----|---------------|
| **A1** | User uploads CSV/XLSX and describes finance workflow | **GREEN** | `file_uploaded` + `author_user_workflow` events; `uploads/input.csv` (7 rows, hash in manifest) | Reference sample is 9 rows; demo run used blind-eval 7-row file — acceptable but note if live-demo uses UI sample | Use same input file in live demo as archived session, or re-run with UI sample |
| **A2** | Backend profiles uploaded file (columns, types, samples, row count) | **GREEN** | `custom_workflow_ingest` event with `column_profiles`, `row_count: 7`, candidate keys | Profiling not surfaced as dedicated UI “SchemaTable” on custom-build path (orchestrator-driven) | Optional: emit/profile card from `custom_workflow_ingest` payload in Author running stage |
| **A3** | Model authors `AuthorOutputContract` | **GREEN** | `contract_planning` model stage; `generated/model_contract_plan.json`, `author_output_contract.json` | — | — |
| **A4** | Model separately reviews contract | **GREEN** | `contract_review` stage; `generated/model_contract_review.json` | No `contract_schema_repair` in this run (not required for pass) | — |
| **A5** | Model authors `generated/agent.py` | **GREEN** | `code_generation` stage; provenance hashes in `model_authoring_summary.md` | — | — |
| **A6** | Model authors `generated/tests/test_agent.py` | **GREEN** | `test_generation` stage; 6 tests collected | — | — |
| **A7** | Backend safety-scans generated code before execution | **AMBER** | Code path: `static_safety_scan` in `author_llm_authoring.py` | **No `static_safety_scan` event** in `d11e6888` `events.jsonl` — compliance by code only | Emit auditable safety-scan decision event on custom-build path |
| **A8** | Backend executes generated code in workspace | **GREEN** | `custom_workflow_execution` event, `exit_code: 0`; `outputs/output.csv`, `outputs/exceptions.csv` | — | — |
| **A9** | Backend runs generated tests/checks | **GREEN** | `pytest_run`: **6/6 passed**; `_fresh_run_author.json` | — | — |
| **A10** | Backend validates universal + contract-specific checks | **GREEN** | `reports/system_validation_report.md` **Overall: PASS** (6 contract checks + pytest tier); exception consistency PASS (5 flagged) | Golden-output tier **SKIPPED** (no independent golden — honest) | Stage independent golden under `evals/` only if claiming golden tier in demo |
| **A11** | Provenance recorded; manifest, events, SESSION_README, archive packaged | **GREEN** | `workflow_completed` via `ai_authored_workflow_build`; `manifest.completion` populated; `archive.zip` 89 KB; `SESSION_README.md` | `manifest.workspace_path` still absolute (export sanitisation may apply on download — verify archive contents) | Confirm exported archive uses sanitised manifest (per `user_facing.py` fixes) |
| **Polish** | Finance-user UX: output preview, exception queue, validation tiers, downloads, honest failure | **AMBER** | UI components exist (`expense-review-queue.tsx`, `output-preview-card.tsx`, four-tier report). **Archived session artifacts:** absolute paths in `reports/validation_report.md`; review decisions in `sessionStorage` only; **~5 min** runtime vs &lt;60 s smoke target | Presentation gaps documented in `expense_author_demo_quality_diagnosis_d11e6888.md`. Code fixes landed **after** session — **not in archive** | Re-run Author once post-polish for clean archive, or demo pre-opened completed URL and disclose 5 min / path / sessionStorage limits |

**Author requirement tally:** 9 GREEN · 2 AMBER · 0 RED (plus Polish AMBER)

---

## Phase 3 — Repair (`invoice_aging_v2`) compliance

**Primary evidence session:** `b8317f51-384a-469a-92d3-90443853a4c5`  
**UI entry:** Dashboard → Repair → **Invoice Aging Boundary Repair** (`invoice_aging_v2`) → bundled `problem_report.md`.

| # | Product behavior requirement | Status | Evidence | Gap | Suggested fix |
|---|------------------------------|--------|----------|-----|---------------|
| **R1** | Load bundled fixture or upload agent ZIP | **GREEN** | `fixture_loaded` `invoice_aging_v2`, 8 files; alternate path: `POST upload_agent_zip` (code + tests) | Live demo uses built-in fixture only in evidence run | — |
| **R2** | Stage files into session workspace | **GREEN** | `working/` tree; `problem_report.md` from fixture | — | — |
| **R3** | Record user problem report | **GREEN** | `primary_problem_resolved` with full v2 report text; source `built_in_sample_problem_report` | — | — |
| **R4** | Inspect files and dependencies | **GREEN** | `repair_report.md` § Files inspected lists agent, data, tests, requirements | Inspection is deterministic pipeline step, not separate tool-loop turn | Acceptable; disclose advisory/deterministic mix in demo |
| **R5** | Reproduce failure or report cannot-reproduce honestly | **GREEN** | `pytest_before_fix`: **5 pass / 2 fail**; logs `reports/before_fix_pytest_output.txt` | Matches fixture baseline (verified locally) | — |
| **R6** | Propose and apply targeted patch when evidence supports | **GREEN** | `patch_applied` `<=31` → `<=30`; `reports/agent_py.patch`; `proposal_source: model` | Patch validated by evidence pipeline, not free-form apply | — |
| **R7** | Post-fix pytest / sample execution | **GREEN** | After-fix **7/0**; `reports/after_fix_pytest_output.txt` | — | — |
| **R8** | Repair report: root cause, before/after, files changed, risks | **GREEN** | `repair_report.md` + `.json`; six sections; post-fix passed **yes** | Remaining-risks wording quality varies across older archives (fixed in code per `final_demo_output_quality_audit.md`) | Use `b8317f51` archive; re-run if presenting provenance fields |
| **R9** | Archive: reports, patch, manifest, events, SESSION_README | **GREEN** | `archive.zip` 20 KB; `completion_via=repair_validated_patch` | — | — |
| **Polish** | Finance UX: evidence gate panel, summary card, ~15 s demo, clear 5/2→7/0 story | **GREEN** | UI gates in `CompletedStage` (`gatesPassed`); **10.6 s** wall time; Repair wizard copy matches v2 boundary bug | Eval scenario **R-01** still targets `invoice_aging_v1` (doc/eval drift, not demo path) | Update eval scenario or README to say demo fixture is v2; optional doc-only |

**Repair requirement tally:** 9 GREEN · 0 AMBER · 0 RED (Polish GREEN with minor doc drift)

---

## Phase 4 — Summary gap table (P0–P3)

| Priority | Gap | Demo | Impact | Suggested action |
|----------|-----|------|--------|------------------|
| **P0** | — | — | No blocking RED on either demo path with fresh artifacts | — |
| **P1** | Author live runtime **~5 min** (DeepSeek pro planning) | Author | Live live demo exceeds &lt;60 s UX target; pre-open completed session or warn | Lead with Repair; Author = pre-baked session URL or accept wait |
| **P1** | Archived Author `validation_report.md` contains **absolute workspace paths** | Author | Unprofessional in downloaded archive (`d11e6888`) | Re-run Author post-`user_facing.py` sanitisation **or** demo from UI without downloading stale report |
| **P1** | **`demo/SCRIPT.md`** still narrates **bank categoriser** + **invoice_aging_v1** date bug | Both | Operator script misaligned with chosen demos | Update SCRIPT.md to expense + v2 (docs-only) |
| **P1** | **`ce21e7e5` / `da9b613a` failure modes** still on disk | Author | Confusion if wrong session linked | Link only `d11e6888` in submission materials |
| **P2** | Safety scan **not auditable** in Author events | Author | INV-adjacent audit gap | Emit `static_safety_scan` event on custom pipeline |
| **P2** | Golden-output tier **SKIPPED** for Author | Author | Honest but weak “independent check” story | Document as intentional; optional golden for expense |
| **P2** | Expense **review decisions** persist in **sessionStorage** only | Author | Resume audit finding (`resume_behavior_audit.md`) | Label in UI copy; do not claim server-backed review state |
| **P2** | High Author token cost (**65k**) | Author | Budget narrative | Disclose; acceptable under 150k cap |
| **P3** | Eval **R-01** uses v1 while demo uses v2 | Repair | CI/regression naming drift | Align scenario or document dual fixtures |
| **P3** | Playwright smoke **deferred** | Both | No browser CI gate | Rely on pytest + manual demo URLs |

---

## Phase 5 — Verdicts, demo order, backup, minimal recommendations

### Verdicts

| Scope | Verdict | Rationale |
|-------|---------|-----------|
| **Author** (`expense_exception_review`) | **AMBER** | Fresh **`completed`** session with full evidence gate (`d11e6888`). Core 11 behaviors met in artifacts. Downgraded for **runtime**, **archived path leakage**, and **polish fixes not reflected in archive**. |
| **Repair** (`invoice_aging_v2`) | **GREEN** | Fresh **`completed`** session (`b8317f51`) with exact 5/2→7/0 story, patch diff, reports, and &lt;11 s runtime. Fixture baseline confirmed. |
| **Overall submission demo** | **AMBER (acceptable with honest framing)** | **Repair-first** packaging is strong. **Author** is evidence-complete but not presentation-pristine without re-run or caveats. |

### Recommended demo order

1. **Repair** (`invoice_aging_v2`) — live or URL `http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5` (~10 s).  
2. **Author** (`expense_exception_review`) — **pre-opened completed** URL `http://localhost:3000/author/d11e6888-886f-4284-8670-522c9a86bbf6` (avoid live 5 min wait). Walk four-tier validation, exceptions CSV, archive download with path caveat.

### Backup sessions / archives

| Demo | Primary | Backup |
|------|---------|--------|
| Repair | `b8317f51-384a-469a-92d3-90443853a4c5` | `72bf763b-fd65-470c-ae6d-7e0ebbeed771` |
| Author | `d11e6888-886f-4284-8670-522c9a86bbf6` | Do **not** use `da9b613a` or `ce21e7e5` for success narrative |

### Minimal recommendations (investigation-only; no implementation here)

1. **Docs sync:** Point `demo/SCRIPT.md` and README demo section at expense + `invoice_aging_v2`.  
2. **One post-polish Author re-run** (optional, ~5 min API cost) to refresh archive without path leakage.  
3. **Submission packet:** Ship Repair + Author session URLs and archive paths from Phase 1 primary table; state Author runtime and golden SKIP explicitly.  
4. **Do not** claim Playwright or eval R-01 as proof of v2 demo without alignment.

---

## Phase 6 — Report back

| Item | Value |
|------|-------|
| **Report path** | `reports/product_behavior_demo_compliance_audit.md` |
| **Author status** | **AMBER** — completed session `d11e6888`; 9/11 GREEN, 2/11 AMBER, polish AMBER |
| **Repair status** | **GREEN** — completed session `b8317f51`; 9/9 GREEN |
| **Overall status** | **AMBER** |
| **P0 gaps** | None (both demos have fresh successful artifact paths) |
| **P1 gaps** | Author ~5 min runtime; archived path leakage; stale `demo/SCRIPT.md`; wrong session IDs in the wild |
| **Next prompt (if continuing)** | *“Update `demo/SCRIPT.md` and README demo section for expense_exception_review + invoice_aging_v2; optionally re-run Author once to refresh `d11e6888` archive after path sanitisation (no validation weakening).”* |

---

*Audit performed read-only against workspace artifacts and committed code. Strict rule applied: code-only without session artifacts graded AMBER (Author A7 safety scan).*
