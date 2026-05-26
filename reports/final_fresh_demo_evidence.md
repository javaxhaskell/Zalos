# Final Fresh Demo Evidence — AgentForge Take-Home

> **Historical report.** Superseded by final evidence session `98942274-b4fb-4e0d-a3b8-0b8144bc01db` and [`final_demo_evidence_summary.md`](./final_demo_evidence_summary.md).

Generated: 2026-05-25 (local fresh runs via live API at `http://127.0.0.1:8000`)

---

## Addendum — post-commit fresh runs (commit `579fa30`)

**When:** 2026-05-25, after `579fa30` (`make down && make up-daemon`, DeepSeek provider, `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD` unset → false)

| Demo | Session ID | Status | Runtime | Key result | Archive path | Presentation verdict |
|------|------------|--------|---------|------------|--------------|----------------------|
| **Repair** (`invoice_aging_v2`) | `b8317f51-384a-469a-92d3-90443853a4c5` | `completed` | **10.6 s** | **5/2 → 7/0** pytest; patch `<=31` → `<=30` | `.workspaces/b8317f51-384a-469a-92d3-90443853a4c5/archive.zip` | **Ready** |
| **Author** (`expense_exception_review`) | `d11e6888-886f-4284-8670-522c9a86bbf6` | `completed` | **314 s (~5.2 min)** | **6/0** generated pytest; **four-tier validation Overall: PASS**; 0 scaffold events | `.workspaces/d11e6888-886f-4284-8670-522c9a86bbf6/archive.zip` | **Ready** |

**Repair details:** `completion_via: repair_validated_patch`, `post_fix_tests_passed: true`, 4,956 tokens (1 advisory model call), `reports/repair_report.md` + `reports/agent_py.patch` present.

**Author details:** DeepSeek pro (planning/review) + flash (codegen/testgen); model stages `contract_planning`, `contract_review`, `code_generation`, `test_generation`; 65,597 tokens; `system_validation_report.md` shows Universal PASS, Contract-specific PASS (6 checks), Generated pytest PASS (6/6), Golden-output SKIPPED (no external golden — correct).

**Honesty:** No code changes during runs; validation not weakened; bank fallback not needed.

**Local URLs (local):**

- Repair: `http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5`
- Author: `http://localhost:3000/author/d11e6888-886f-4284-8670-522c9a86bbf6`

**Packaging verdict:** **Both flows presentation-ready** on this commit.

---

## Summary table

| Demo | Session ID | Status | Workflow | Archive path | Presentation verdict |
|------|------------|--------|----------|--------------|----------------------|
| **Repair** (`invoice_aging_v2`) | `72bf763b-fd65-470c-ae6d-7e0ebbeed771` | `completed` | repair | `.workspaces/72bf763b-fd65-470c-ae6d-7e0ebbeed771/archive.zip` | **Ready** — full happy path, strong operating example |
| **Author primary** (`expense_exception_review`) | `da9b613a-7626-4f25-ba6b-7be7775547b6` | `failed_other` | author / custom upload | `.workspaces/da9b613a-7626-4f25-ba6b-7be7775547b6/archive.zip` (partial) | **Not ready** — pipeline ran but session crashed on pytest gate |
| **Author fallback** (`bank_transaction_categorisation`) | `d931c367-dbec-436c-93d8-4ef94e77dfbd` | `failed_other` | author / `bank_categoriser` hint | `.workspaces/d931c367-dbec-436c-93d8-4ef94e77dfbd/archive.zip` | **Partial** — good for validation tiers + honest failure UX, not a completion demo |

---

## 1. Repair evidence

**Path:** `POST /sessions` → `POST /sessions/{id}/load_fixture/invoice_aging_v2` → `POST /sessions/{id}/run` (problem report from bundled fixture)

| Check | Expected | Observed |
|-------|----------|----------|
| Pre-fix pytest | 5 pass / 2 fail | **5 pass / 2 fail** |
| Root cause | Boundary bug at 31 days | **`if days_overdue <= 31:` mis-buckets 31-day invoices into 1-30** |
| Patch | `<=31` → `<=30` | **Applied:** `if days_overdue <= 30:` (old `<= 31` removed) |
| Post-fix pytest | 7 pass / 0 fail | **7 pass / 0 fail** |
| Reports | repair_report.md + .json | **Present** (`reports/repair_report.md`, `reports/repair_report.json`) |
| Patch diff | auditable | **`reports/agent_py.patch`** |
| Before/after logs | pytest output | **`reports/before_fix_pytest_output.txt`**, **`reports/after_fix_pytest_output.txt`** |
| Session status | completed | **`completed`** |
| Archive | downloadable | **`archive.zip`** (19 KB; built via `GET /sessions/{id}/archive.zip`) |
| Wall time | — | **~13 s** |
| Tokens | — | **5,137** (1 advisory model call; deterministic evidence pipeline owns the fix) |

**Provenance highlights**

- `advisory_mode_enabled` event documents LLM-as-advisory, orchestrator-owned repair pipeline.
- `repair_proposal.source = model` with evidence-linked root cause and failing test names.
- `manifest.json` → `completion_via: repair_validated_patch`, `post_fix_tests_passed: true`.
- No `reference_sample_scaffold_mode` or scaffold events.

**Remaining risks (from repair report)**

- Report states “No remaining risk” for the boundary fix; other buckets unchanged.
- Operator should note repair root-cause inference is pattern-matched from fixture evidence (documented in architecture audits), not purely free-form LLM diagnosis.

**Presentation readiness:** **Yes.** Load `invoice_aging_v2` in the Repair wizard, describe the 31-day boundary symptom, and walk through before/after pytest + patch diff + archive download.

---

## 2. Author evidence

### 2a. Primary — `expense_exception_review` (DeepSeek, no scaffold)

**Input:** `blind_eval_cases/expense_exception_review/input.csv` (7 rows)  
**Prompt:** `blind_eval_cases/expense_exception_review/prompt.txt`  
**API path:** create author session → upload CSV → `POST /run` with prompt only (no `template_hint`)

| Stage | Result |
|-------|--------|
| Contract planning / review | **Completed** (DeepSeek pro) |
| Code generation / test generation | **Completed** (DeepSeek flash) |
| Agent execution | **Exit 0** — `outputs/output.csv`, `outputs/exceptions.csv`, `reports/validation_report.md` produced |
| Generated pytest | **4 pass / 1 fail** — `test_output_csv_structure` |
| Four-tier validation report | **Not reached** — session aborted before `system_validation_report` |
| Final status | **`failed_other`** — `AttributeError: 'str' object has no attribute 'filter'` |

**Model stages (events):** `contract_planning`, `contract_review`, `code_generation`, `test_generation`  
**Tokens:** 51,020 · **Wall time:** ~4.4 min · **Scaffold events:** 0

**Artifacts preserved despite failure**

- `generated/agent.py`, `generated/tests/test_agent.py`, `generated/author_output_contract.json`
- `reports/model_authoring_summary.md` (provenance hashes for all model files)
- Partial `archive.zip` (75 KB)

### 2b. Fallback — `bank_transaction_categorisation` (DeepSeek, no scaffold)

**Input:** `apps/web/app/api/reference-samples/bank-categoriser/bank_transaction_categorisation_demo.csv` (18 rows)  
**Prompt:** Author wizard bank template prompt (same text as UI)  
**API path:** create author session → upload CSV → `POST /run` with `template_hint: bank_categoriser`

| Stage | Result |
|-------|--------|
| Model pipeline | **All four stages completed** |
| Agent execution | **Exit 0** — 18-row output with categories |
| Generated pytest | **6 pass / 1 fail** — `test_validation_report_has_category_summary` (expects snake_case metric keys in report text; agent wrote human-readable table headers) |
| Execution repair attempt | **Failed** — repair candidate pytest pathing error (`generated/repairs/generated/agent.py` not found) |
| Four-tier validation | **Ran (partial pass)** — see below |
| Final status | **`failed_other`** / `generated_pytest_failed` |

**Tokens:** 58,683 · **Wall time:** ~4 min · **Scaffold events:** 0

**Four-tier validation (`reports/system_validation_report.md`)**

| Tier | Checks | Status |
|------|--------|--------|
| Universal | Required deliverables, row-level invariants | **PASS** |
| Contract-specific | Output schema, required columns, allowed enums | **PASS** |
| Generated pytest | Model-authored workflow tests | **FAIL** (1/7) |
| Golden output | Independent expected CSV | **SKIPPED** (no external golden staged — correct) |

**UI tier grouping:** Markdown report uses headings *Universal validation*, *Contract-specific validation*, *Generated pytest*, *Golden-output comparison* with per-check `Tier:` labels — suitable for demo of validation architecture even on failure.

---

## 3. Honesty checks

| Check | Result |
|-------|--------|
| `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD` | **Not set in `.env`** → defaults **false**; zero scaffold events in all three sessions |
| DeepSeek configured | **`GET /health/ready`** → `deepseek_api_key: configured`, stage models pro/flash as expected |
| API + web running | **`make dev-health`** → API 200, web 200 |
| Secrets in logs/reports | **No API keys printed**; env grep redacted; model summary lists provider/model names only |
| Validation weakened | **No** — failures surfaced honestly (`generated_pytest_failed`, pytest gate blocked completion) |
| Fake outputs | **No** — outputs produced by executed `generated/agent.py`, not stubbed |
| Old archives edited | **No** |
| Code patched before/during runs | **No** |
| AR/vendor/broad stress tests | **Not run** |
| Repeated attempts | **One expense attempt, one bank fallback** (fallback only after expense blocked) |

---

## 4. Limitations

1. **No fresh completed Author session** on 2026-05-25 runs. Both expense and bank paths failed the generated-pytest completion gate.
2. **Expense run backend bug:** After pytest failure, orchestrator crashed with `AttributeError: 'str' object has no attribute 'filter'` instead of a typed `generated_pytest_failed` failure — prevents four-tier report and clean failure card on that path.
3. **Bank run:** Agent output and validation report are materially good (18 rows preserved, category table present), but model-generated test expects snake_case metric tokens (`transaction_count`, etc.) while the agent wrote a markdown table with human headers — strict test/agent mismatch, not a data-quality failure.
4. **Bank execution repair:** Repair candidate tests reference wrong agent path under `generated/repairs/` — secondary failure after initial pytest miss.
5. **Repair advisory LLM:** One DeepSeek call for advisory INFO; deterministic inference + evidence gates apply the patch — acceptable but should be disclosed in live demo.
6. **Author completion metadata gap on bank run:** `manifest.completion.model_stages` empty despite event-log provenance — presentation should use `model_authoring_summary.md` / events for stage evidence.

---

## 5. Demo recommendation

| Flow | Recommend for live demo? | Notes |
|------|--------------------------|-------|
| **Repair (`invoice_aging_v2`)** | **Yes — primary demo** | Reliable ~15 s completion; clear 5/2 → 7/0 story; patch diff + archive |
| **Author (expense)** | **No — blocked** | Shows model pipeline + artifacts but crashes on failure handling; confuses “honest failure” story |
| **Author (bank fallback)** | **Optional secondary** | Use to show four-tier validation UI, model stages, tokens, and **honest non-completion** when generated pytest fails; do not claim end-to-end Author success |
| **Packaged video / submission** | Lead with Repair; if showing Author, use bank session failure + validation tiers and state limitation openly |

**Suggested local URLs (local)**

- Repair: `http://localhost:3000/repair/72bf763b-fd65-470c-ae6d-7e0ebbeed771`
- Author (bank fallback): `http://localhost:3000/author/d931c367-dbec-436c-93d8-4ef94e77dfbd`

---

## REPORT BACK

| Item | Value |
|------|-------|
| **Repair session ID** | `72bf763b-fd65-470c-ae6d-7e0ebbeed771` |
| **Repair status** | `completed` |
| **Repair archive path** | `/Users/arhamshuaib/Desktop/Zalos/.workspaces/72bf763b-fd65-470c-ae6d-7e0ebbeed771/archive.zip` |
| **Repair presentation verdict** | **Ready** |
| **Author session ID** | Primary: `da9b613a-7626-4f25-ba6b-7be7775547b6` · Fallback: `d931c367-dbec-436c-93d8-4ef94e77dfbd` |
| **Author workflow** | Primary: `expense_exception_review` (custom upload) · Fallback: `bank_transaction_categorisation` (`bank_categoriser` hint) |
| **Author status** | Both `failed_other` |
| **Author archive path** | `/Users/arhamshuaib/Desktop/Zalos/.workspaces/da9b613a-7626-4f25-ba6b-7be7775547b6/archive.zip` (partial) · `/Users/arhamshuaib/Desktop/Zalos/.workspaces/d931c367-dbec-436c-93d8-4ef94e77dfbd/archive.zip` |
| **Author presentation verdict** | **Not ready** (primary) · **Partial** (fallback — validation/failure UX only) |
| **Final evidence report path** | `/Users/arhamshuaib/Desktop/Zalos/reports/final_fresh_demo_evidence.md` |
| **Blockers** | (1) No fresh completed Author run; (2) expense pytest gate + backend `AttributeError` on failure path; (3) bank generated-pytest strict report-text assertion |
| **Ready for packaging** | **No** — Repair evidence is submission-ready; Author needs a passing fresh run or explicit failure-only packaging with limitations |

---

## STEP 0 — Pre-run confirmation (recorded)

- Scaffold: `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD` unset → **false**
- Provider: `LLM_PROVIDER=deepseek`, `GET /health/ready` **ready**
- Services: `make dev-status` → API :8000, web :3000 running
- Secrets: not exposed in this report or command output

## STEP 4 — Validation commands

No code changes were made during this evidence run. Validation commands were **not** re-run.
