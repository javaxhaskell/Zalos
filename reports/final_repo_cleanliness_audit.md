# Final Repo Cleanliness Audit

**Date:** 2026-05-26  
**Scope:** workspace repo cleanup (audit + minimal safe cleanup; no commits)  
**Final demo sessions (must preserve):**
- Author GREEN: `98942274-b4fb-4e0d-a3b8-0b8144bc01db`
- Repair GREEN: `b8317f51-384a-469a-92d3-90443853a4c5`

**Git snapshot:** 76 tracked changes (40 modified/deleted + 36 untracked dirs/files in status), 90 untracked paths, 240 ignored paths.

---

## Phase 1 — Path classification

**Legend:** **Must commit** = submission-critical code/docs/fixtures. **Should commit** = valuable evidence or supporting artifacts. **Do not commit** = local-only, machine-specific, or superseded scratch. **Dangerous** = secrets or would break demos if deleted.

### Modified tracked paths (40)

| Path | Status | Classification | Rationale |
|------|--------|----------------|-----------|
| `apps/api/openapi.snapshot.json` | M | Must commit | API contract drift from session/cancel/fault-tolerance endpoints |
| `apps/api/src/agentforge/api/routers/sessions.py` | M | Must commit | Cancel, resume, mitigation read paths |
| `apps/api/src/agentforge/orchestrator/__init__.py` | M | Must commit | Orchestrator exports |
| `apps/api/src/agentforge/orchestrator/author_custom_build.py` | M | Must commit | Author build + golden staging + sanitisation |
| `apps/api/src/agentforge/orchestrator/author_llm_authoring.py` | M | Must commit | LLM-first author pipeline (expense + bank guidance) |
| `apps/api/src/agentforge/orchestrator/repair_flow.py` | M | Must commit | Repair v2 evidence path |
| `apps/api/src/agentforge/orchestrator/runner.py` | M | Must commit | Workflow runner integration |
| `apps/api/src/agentforge/persistence/archive.py` | M | Must commit | Archive export behaviour |
| `apps/api/src/agentforge/persistence/user_facing.py` | M | Must commit | Path sanitisation for user-facing reports |
| `apps/api/src/agentforge/schemas/__init__.py` | M | Must commit | Schema exports |
| `apps/api/src/agentforge/schemas/responses.py` | M | Must commit | API response models |
| `apps/api/src/agentforge/schemas/session.py` | M | Must commit | Failure mitigation / session fields |
| `apps/api/src/agentforge/validation/reporter.py` | M | Must commit | Four-tier validation reporting |
| `apps/api/tests/test_author_codegen_reliability.py` | M | Must commit | Author regression tests |
| `apps/api/tests/test_author_validation_architecture.py` | M | Must commit | Validation architecture tests |
| `apps/api/tests/test_final_demo_output_quality.py` | M | Must commit | Demo output quality gates |
| `apps/api/tests/test_reference_sample_honesty.py` | M | Must commit | Reference sample contract tests |
| `apps/web/app/about/page.tsx` | M | Must commit | About / local walkthrough UI |
| `apps/web/app/author/[sid]/page.tsx` | M | Must commit | Author wizard UX |
| `apps/web/app/page.tsx` | M | Must commit | Dashboard |
| `apps/web/app/repair/[sid]/page.tsx` | M | Must commit | Repair wizard UX |
| `apps/web/package.json` | M | Must commit | Radix dropdown dependency |
| `apps/web/src/components/author-intent-mismatch-card.tsx` | M | Must commit | Author failure UX |
| `apps/web/src/components/category-breakdown.tsx` | M | Must commit | Output preview |
| `apps/web/src/components/data-preview-table.tsx` | M | Must commit | Expense review queue + table UX |
| `apps/web/src/components/failure-card.tsx` | M | Must commit | Failure mitigation display |
| `apps/web/src/components/finance-summary-card.tsx` | M | Must commit | Summary cards |
| `apps/web/src/components/output-preview-card.tsx` | M | Must commit | Output preview refactor |
| `apps/web/src/components/recent-sessions.tsx` | M | Must commit | Dashboard sessions |
| `apps/web/src/components/resume-banner.tsx` | M | Must commit | Resume UX |
| `apps/web/src/components/save-progress-button.tsx` | D | Must commit (deletion) | Replaced by resume banner pattern |
| `apps/web/src/components/ui/button.tsx` | M | Must commit | Button variants |
| `apps/web/src/components/user-question-panel.tsx` | M | Must commit | Date clarification / Q&A |
| `apps/web/src/components/workflow-picker.tsx` | M | Must commit | Workflow selection |
| `demo/SCRIPT.md` | M | Must commit | live demo script (updated this audit) |
| `packages/shared-schemas/src/generated.ts` | M | Must commit | OpenAPI TS mirror |
| `pnpm-lock.yaml` | M | Must commit | Lockfile |
| `reports/author_blind_eval_report.json` | M | Should commit | Blind eval evidence |
| `reports/author_blind_eval_report.md` | M | Should commit | Blind eval summary |
| `reports/final_fresh_demo_evidence.md` | M | Should commit | Demo evidence (partially superseded by `final_demo_evidence_summary.md`) |
| `README.md` | M* | Must commit | *Run section updated this audit to expense + v2 |

### Untracked paths (90) — grouped

| Group / path | Classification | Rationale |
|--------------|----------------|-----------|
| `.workspaces-deepseek-rerun/**` (36 files) | Do not commit | Local DeepSeek rerun workspace; now gitignored |
| `apps/api/src/agentforge/fault_tolerance/**` | Must commit | User mitigation mapping (INV-6 read-time) |
| `apps/api/src/agentforge/orchestrator/author_date_clarification.py` | Must commit | Date clarification orchestration |
| `apps/api/src/agentforge/orchestrator/tool_scope_audit.py` | Must commit | Tool scope audit events |
| `apps/api/tests/test_author_date_clarification*.py` | Must commit | Date clarification tests |
| `apps/api/tests/test_fault_tolerance_user_mitigation.py` | Must commit | Fault tolerance tests |
| `apps/api/tests/test_session_cancel_endpoint.py` | Must commit | Cancel endpoint tests |
| `apps/api/tests/test_session_state_and_resume_docs.py` | Must commit | Resume doc contract tests |
| `apps/api/tests/test_tool_scope_audit_events.py` | Must commit | Tool scope audit tests |
| `apps/web/app/api/reference-samples/expense-exception-review/**` | Must commit | Author reference sample API |
| `apps/web/app/api/reference-samples/invoice-aging/**` | Must commit | Repair reference sample API |
| `apps/web/src/components/cancel-workflow-button.tsx` | Must commit | Cancel UX |
| `apps/web/src/components/expense-review-queue.tsx` | Must commit | Expense review queue |
| `apps/web/src/components/feature-walkthrough/**` (16 files) | Must commit | About page animated walkthrough |
| `apps/web/src/components/reviewed-output-download-menu.tsx` | Must commit | Download menu |
| `apps/web/src/components/ui/dropdown-menu.tsx` | Must commit | Radix dropdown primitive |
| `docs/FAULT_TOLERANCE_MODEL.md` | Must commit | Fault tolerance architecture doc |
| `docs/SESSION_STATE_AND_RESUME.md` | Must commit | Resume behaviour doc |
| `docs/TOOL_SCOPE_MODEL.md` | Must commit | Tool scope doc |
| `evals/golden/expense_exception_review/expected_output.csv` | Must commit | Independent golden for expense demo |
| `reports/_fresh_*.json` (4 files) | Do not commit | Machine-local run metadata with absolute paths; now gitignored |
| `reports/expense_author_*`, `reports/final_*`, `reports/fault_tolerance_*`, `reports/product_behavior_*`, `reports/resume_*`, `reports/tool_scope_*` (13 markdown) | Should commit | operator evidence and implementation audits |

### Ignored paths (240) — pattern summary

| Pattern | Count (approx) | Classification | Rationale |
|---------|------------------|----------------|-----------|
| `.env`, `apps/api/.env` | 2 | Dangerous if committed | Secrets; correctly ignored and untracked |
| `.venv/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | many | Do not commit | Standard local caches |
| `.workspaces/` (incl. `98942274`, `b8317f51`) | many | Do not commit | Session workspaces; **must not delete** — demo URLs depend on them |
| `.workspaces-blind-eval/**` | ~80 dirs | Do not commit | Blind eval scratch workspaces |
| `.workspaces-deepseek-benchmark/**` | dirs | Do not commit | Benchmark workspaces |
| `.workspaces-deepseek-rerun/**` | 1 session | Do not commit | Rerun scratch; gitignored this audit |
| `apps/web/.next-build/`, `.next-dev/`, `tsconfig.tsbuildinfo` | few | Do not commit | Frontend build artifacts |
| `uv.lock` (root + apps/api) | 2 | Do not commit | Lockfiles gitignored by policy |
| `*.log` under reports | 1 | Do not commit | Local log |
| `.DS_Store` | several | Do not commit | OS metadata |

---

## Phase 2 — Secret and local path audit

### Secret scan (no values printed)

| Check | Result |
|-------|--------|
| `.env` tracked? | **No** — `git ls-files` count 0; ignored via `.gitignore:64` |
| `apps/api/.env` tracked? | **No** — ignored |
| Real `sk-ant-…` keys in tracked source (excl. `.env*`, tests using `sk-ant-test`) | **None found** |
| Hardcoded `password=` / `secret_key=` in py/ts/json | **None found** |
| Documentation references to `sk-ant-…` | Placeholder examples only (`README.md`, `DEPLOYMENT.md`, test fixtures) |

**Verdict:** **GREEN** — secrets correctly excluded; no leaked keys in committed tree.

### Local absolute path scan (`/Users/arhamshuaib`)

| Location | Risk | Action |
|----------|------|--------|
| `reports/_fresh_*.json` | Machine-specific workspace paths | Gitignored (do not commit) |
| `HANDOFF.md`, older diagnosis reports | Dev handoff paths | Acceptable in internal docs; not submission front door |
| Test fixtures with captured stderr paths | Regression fixtures embedding workspace paths | Acceptable — tests assert sanitisation behaviour |
| Green session `98942274` archived reports | User-facing reports use relative paths per `final_expense_author_green_evidence.md` | **GREEN** |
| Superseded sessions (`d11e6888`, `4a9a4343`) | Archived absolute paths in validation_report | Historical evidence only — do not use as primary demo |

**Verdict:** **AMBER** — no secrets; path leakage fixed for green Author session; stale evidence reports retain historical absolute paths by design.

---

## Phase 3 — Structure / modularity audit (findings only, no refactor)

| Priority | Finding | Impact | Recommendation |
|----------|---------|--------|----------------|
| **P0** | `author_llm_authoring.py` ~420 KB / ~10k LOC | Hard to review; high merge conflict risk | Future ADR: split by stage (contract, codegen, testgen, repair) — **not done this pass** |
| **P0** | `data-preview-table.tsx` ~42 KB | UI monolith mixing table, review queue, downloads | Extract subcomponents in follow-up PR |
| **P1** | Bank categoriser + expense exception logic coexist in author orchestrator | Cognitive load for operators | Documented in code comments; bank path disabled by default |
| **P1** | 13+ new `reports/*.md` evidence files overlap | Operator confusion on canonical evidence | **Canonical:** `final_demo_evidence_summary.md`, `final_expense_author_green_evidence.md`, `final_repair_archive_inspection.md` |
| **P2** | Eval `R-01` uses `invoice_aging_v1`; demo uses `invoice_aging_v2` | Documented drift; CI ≠ live demo fixture | Keep documented in `demo/SCRIPT.md` § Honest limitations |
| **P2** | `product_behavior_demo_compliance_audit.md` still cites `d11e6888` as primary | Stale vs green session | Treat as historical; prefer `final_expense_author_green_evidence.md` |
| **P3** | `feature-walkthrough/` mockup panels (16 files) | Good modularity for About page | No action |
| **P3** | New `fault_tolerance/` package (2 modules) | Clean extraction | Good pattern for future splits |

---

## Phase 4 — Docs consistency audit

### Canonical demo sessions (target state)

| Flow | Primary session | URL |
|------|-----------------|-----|
| Author | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` | `/author/98942274-b4fb-4e0d-a3b8-0b8144bc01db` |
| Repair | `b8317f51-384a-469a-92d3-90443853a4c5` | `/repair/b8317f51-384a-469a-92d3-90443853a4c5` |

### Stale references and recommended replacements

| File | Stale content | Replacement |
|------|---------------|-------------|
| `README.md` Run section | Bank categoriser + `invoice_aging_v1` primary walkthrough | **Fixed this audit** → expense exception + `invoice_aging_v2` + green session URLs |
| `demo/SCRIPT.md` | `d11e6888` as known-good; golden SKIPPED | **Fixed this audit** → `98942274`; four-tier PASS incl. golden |
| `reports/final_fresh_demo_evidence.md` | Lists `d11e6888` as Author primary | Point readers to `final_demo_evidence_summary.md` / `final_expense_author_green_evidence.md` |
| `reports/product_behavior_demo_compliance_audit.md` | `d11e6888` primary, AMBER verdict | Superseded by GREEN evidence report |
| `docs/LOCAL_WALKTHROUGH.md` | Bank CSV as example upload | Add expense exception path as primary; retain bank as secondary reference |
| `docs/SUBMISSION_CHECKLIST.md` | Bank CSV listed first | Add expense golden + v2 repair as primary demo paths |
| `docs/TAKE_HOME_REQUIREMENTS_COVERAGE.md` | Bank-first wording | Align primary demo with expense + v2 (bank remains valid eval A-01) |
| `README.md` Tests section | A-01 bank / R-01 v1 eval scenarios | Accurate for CI; add note that live demo uses expense + v2 (eval drift documented) |
| `agentforge-docs-and-demo` skill Run section | Bank + v1 | Skill text lags product; README + `demo/SCRIPT.md` are submission truth |

### Consistency checks

| Topic | Status |
|-------|--------|
| Synthetic data only (INV-9) | **PASS** — stated in README, demo script, About |
| Golden PASS on green Author | **PASS** — `evals/golden/expense_exception_review/` + session `98942274` |
| Retry / resume semantics | **PASS** — `docs/SESSION_STATE_AND_RESUME.md`, resume banner, tests |
| Final demo workspaces on disk | **PASS** — both `.workspaces/98942274…` and `.workspaces/b8317f51…` exist |
| OpenAPI / TS schema sync | **PASS** — modified snapshot + generated.ts in diff |

**Docs verdict:** **GREEN** — README, demo script, local walkthrough, submission checklist, and final evidence summary aligned to green sessions `98942274` + `b8317f51`.

---

## Phase 9 — Final docs-cleanup pass (2026-05-26)

**Scope:** Docs-only consistency cleanup. No core product code changes, no commits.

### Files changed

| File | Change |
|------|--------|
| `docs/LOCAL_WALKTHROUGH.md` | Repair-first demo order; Expense Exception Review as primary Author path; green session URLs; golden validation-only; retry/resume semantics; reference-sample honesty |
| `docs/SUBMISSION_CHECKLIST.md` | Primary demo table (Author GREEN `98942274`, Repair GREEN `b8317f51`); expense + v2 as primary paths; UX semantics section |
| `reports/final_demo_evidence_summary.md` | Rewritten to canonical GREEN evidence (98942274, golden 7/7, 5/7 flagged) |
| `reports/product_behavior_demo_compliance_audit.md` | Superseded notice at top pointing to `98942274` + `final_demo_evidence_summary.md` |
| `reports/final_fresh_demo_evidence.md` | Superseded notice at top |
| `demo/SCRIPT.md` | 5/5 pytest, golden 7/7, 5/7 flagged rows; reference-sample/retry/UI-triage messaging |
| `README.md` | Demo order, golden validation-only, eval drift notes, Author GREEN session reference |
| `reports/final_repo_cleanliness_audit.md` | This section |

### Stale refs fixed

| Stale | Replacement |
|-------|-------------|
| Author primary = bank categoriser | Expense Exception Review |
| Author session `d11e6888`, `4a9a4343` as primary | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` (GREEN) |
| Author verdict AMBER | GREEN (four-tier PASS, golden 7/7) |
| pytest 6/6 or 3/3 | 5/5 |
| golden SKIPPED on Author | golden PASS 7/7 on green session |
| Repair primary = invoice_aging_v1 | `invoice_aging_v2` (unchanged on repair session) |
| Local walkthrough bank CSV upload as primary | Expense Exception Review path |

### Remaining acceptable historical mentions

| Location | Term | Classification |
|----------|------|----------------|
| `README.md` | `bank_categoriser`, `invoice_aging_v1`, eval A-01/R-01 | **Acceptable** — CI regression paths; explicitly distinguished from live demo |
| `reports/product_behavior_demo_compliance_audit.md` | Full body cites `d11e6888`, AMBER | **Historical** — superseded notice at top |
| `reports/final_fresh_demo_evidence.md` | Failed runs, old session IDs | **Historical** — superseded notice at top |
| `reports/final_expense_author_archive_inspection.md` | `4a9a4343` AMBER inspection | **Historical** — diagnostic for superseded session |
| `reports/expense_author_semantic_gap_diagnosis_*.md` | Prior session analysis | **Historical** — diagnostic artifacts |
| Project layout in README | `bank_categoriser/`, `invoice_aging_v1/` dirs | **Acceptable** — repo structure truth |

### Validation (this pass)

```bash
cd apps/web && npm run typecheck && npm run build
# Exit 0 — typecheck passed; Next.js 14.2.35 production build succeeded (2026-05-26 docs-cleanup pass)
```

### Docs verdict (final)

| Dimension | Verdict |
|-----------|---------|
| **Docs consistency** | **GREEN** |
| **Submission readiness (docs)** | **GREEN** — canonical evidence in `final_demo_evidence_summary.md`; historical reports marked superseded |

---

## Phase 5 — Minimal safe cleanup performed

| Action | Files touched |
|--------|---------------|
| Added `.workspaces-deepseek-rerun/` to `.gitignore` | `.gitignore` |
| Added `reports/_fresh_*.json` to `.gitignore` | `.gitignore` |
| Updated Author demo session + golden tier in demo script | `demo/SCRIPT.md` |
| Updated Run section to expense + v2 + green URLs | `README.md` |

**Not done (by design):**
- No file deletes
- No fixture/golden/workspace removal
- No Author/Repair core refactors
- No validation weakening
- No commits

---

## Phase 6 — `.gitignore` review

| Rule | Correct? | Notes |
|------|----------|-------|
| `.workspaces/` | Yes | Preserves demo sessions locally |
| `.workspaces-blind-eval/` | Yes | Eval scratch |
| `.workspaces-deepseek-rerun/` | **Added** | Was missing; caused 36 untracked workspace files |
| `evals/reports/*` !example | Yes | Keeps example only |
| `evals/golden/**` | **Not ignored** | Correct — expense golden must remain committable |
| `fixtures/**` CSVs | **Not ignored** | Correct |
| `.env.example` | **Not ignored** | Correct |
| `docs/**`, `reports/**` | **Not ignored** | Correct — evidence committable |
| `reports/_fresh_*.json` | **Added** | Machine-local run metadata |

---

## Phase 7 — Validation commands

```bash
python -m compileall apps/api/src apps/api/tests -q
# Exit 0

cd apps/web && npm run typecheck && npm run build
# Exit 0 — Next.js build succeeded

cd apps/api && python -m pytest \
  tests/test_fault_tolerance_user_mitigation.py \
  tests/test_session_state_and_resume_docs.py \
  tests/test_tool_scope_audit_events.py \
  tests/test_reference_sample_honesty.py \
  tests/test_author_validation_architecture.py \
  tests/test_final_demo_output_quality.py -q
# 72 passed

# Extended author suite (core code in diff):
python -m pytest \
  tests/test_author_codegen_reliability.py \
  tests/test_author_date_clarification.py \
  tests/test_author_date_clarification_ui_contract.py \
  tests/test_session_cancel_endpoint.py -q
# 118 passed
```

**Validation verdict:** **GREEN**

---

## Phase 8 — Final readiness verdict

| Dimension | Verdict | Notes |
|-----------|---------|-------|
| **Cleanliness** | **AMBER** | Large feature diff ready to commit; local workspaces/caches correctly ignored; `_fresh_*.json` no longer pollutes status |
| **Modularity** | **AMBER** | New packages clean; `author_llm_authoring.py` + `data-preview-table.tsx` are review hotspots |
| **Docs** | **GREEN** | README, demo script, local walkthrough, submission checklist, final evidence summary aligned to green sessions |
| **Secrets** | **GREEN** | `.env` untracked; no key leakage |
| **Demos** | **GREEN** | Both green workspaces present; URLs documented |
| **Tests / build** | **GREEN** | compileall, typecheck, build, 190 pytest passed |
| **Submission readiness** | **GREEN** | Docs aligned; safe to commit feature branch after staging Must/Should commit paths |

### Recommended commit staging (when ready)

**Must commit:** All modified app code, tests, schemas, `evals/golden/expense_exception_review/`, new docs under `docs/`, frontend components, `demo/SCRIPT.md`, `README.md`, `openapi.snapshot.json`, `pnpm-lock.yaml`.

**Should commit:** Evidence reports (`reports/final_*`, `reports/tool_scope_*`, etc.); updated blind eval reports.

**Do not commit:** `.workspaces*`, `.env`, caches, `_fresh_*.json`, `.workspaces-deepseek-rerun/`.

---

*Audit artifact: `reports/final_repo_cleanliness_audit.md`*
