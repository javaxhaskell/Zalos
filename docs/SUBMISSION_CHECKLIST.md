# Requirements checklist

This checklist maps assignment requirements to concrete implementation evidence in the current AgentForge codebase. It is intentionally scoped to the existing product: no real banking, accounting, payment, or billing APIs are called; all bundled data is synthetic.

## Primary demo paths (canonical)

| Flow | Workflow | Session | Verdict |
|------|----------|---------|---------|
| **Author** | Expense Exception Review | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` | **GREEN** — 5/5 pytest, golden 7/7, four-tier PASS, 5/7 flagged (EXP-001,002,004,005,007) |
| **Repair** | `invoice_aging_v2` boundary bug | `b8317f51-384a-469a-92d3-90443853a4c5` | **GREEN** — 5/2 → 7/0 pytest, patch `<=31` → `<=30` |

**Demo order:** Repair live first (~10 s); Author pre-opened completed URL (~3 min walkthrough).

Evidence summary: [`reports/final_demo_evidence_summary.md`](../reports/final_demo_evidence_summary.md).

## Product workflows

| Requirement | Current evidence |
|---|---|
| Author a new financial agent from a workflow description and CSV/XLSX sample | Author wizard at `apps/web/app/author/[sid]/page.tsx`; backend `AuthorFlow` and LLM-first custom build in `apps/api/src/agentforge/orchestrator/author_flow.py` and `author_custom_build.py`. **Primary demo:** Expense Exception Review (not bank categoriser). |
| Repair an existing agent from an uploaded folder/ZIP plus problem report | Repair wizard at `apps/web/app/repair/[sid]/page.tsx`; backend `RepairFlow` in `apps/api/src/agentforge/orchestrator/repair_flow.py`. **Primary demo:** `invoice_aging_v2` (not v1). |
| Finance-user UI for both workflows | Dashboard `apps/web/app/page.tsx`, workflow cards in `apps/web/src/components/workflow-picker.tsx`, progress/status/artifact/audit components under `apps/web/src/components/` |
| File-enabled workspace | Per-session `.workspaces/<session-id>/` managed by `apps/api/src/agentforge/persistence/workspace.py`; uploads, generated files, outputs, reports, manifest, event log, and archive live under the workspace |
| Synthetic sample files | Expense sample: `blind_eval_cases/expense_exception_review/input.csv` + UI reference sample at `/api/reference-samples/expense-exception-review`; bank CSV under `templates/bank_categoriser/data/` (eval A-01 only); repair fixtures under `fixtures/broken_agents/invoice_aging_v2/` (primary) and `invoice_aging_v1/` (historical) |
| Tests or golden-output checks | Author: model-generated pytest + four-tier validation including independent golden at `evals/golden/expense_exception_review/` (validation-only, not in codegen). Repair: before/after pytest and golden evidence in `apps/api/tests/test_repair_evidence_gate.py` |
| Setup and run instructions | `README.md`, `docs/LOCAL_WALKTHROUGH.md`, and this checklist |
| AI coding assistant transcript or summary | [`TRANSCRIPT.md`](../TRANSCRIPT.md) |

## Author behaviour

Successful Author completion is intentionally gated. A successful Author run must include:

- `model_calls > 0`
- model stage `contract_planning`
- model stage `contract_review`
- model stage `code_generation` or `code_adaptation`
- model stage `test_generation` or equivalent generated checks
- `generated/model_contract_plan.json`
- `generated/model_contract_review.json`
- model-authored `generated/agent.py`
- model-authored `generated/tests/test_agent.py` or model-authored checks
- generated code execution evidence
- generated tests/checks passing
- `reports/model_authoring_summary.md`
- `reports/validation_report.md`
- `manifest.json`, `events.jsonl`, `SESSION_README.md`, and `archive.zip`

Backend evidence: `apps/api/src/agentforge/orchestrator/author_llm_authoring.py`, `author_custom_build.py`, and `author_contract_validation.py`.

The deterministic backend profiles files, runs generated code/tests, validates against the model-authored contract, records provenance, and packages artifacts. It does not complete Author by copying checked-in workflow code. **Reference samples provide prompt context only — they do not bypass the LLM-first Author path.**

**Model authors spec; validators enforce:** The model authors the output contract, agent code, and tests. Backend validators enforce universal checks, contract-specific checks, generated pytest, and independent golden comparison when staged.

## Repair behaviour

The Repair path supports:

- built-in broken fixture load (`invoice_aging_v2` recommended)
- uploaded agent ZIP staging
- user problem report capture
- file/dependency inspection
- before-fix reproduction or honest cannot-reproduce result
- likely root-cause and risk reporting
- targeted patch proposal/application
- after-fix pytest/sample execution
- `reports/repair_report.md`, `reports/repair_report.json`, patch artifacts, manifest, event log, and archive

Backend evidence: `apps/api/src/agentforge/orchestrator/repair_flow.py`, repair tools under `apps/api/src/agentforge/tools/`, and tests under `apps/api/tests/test_repair_*`.

## Technical requirements

| Requirement | Evidence |
|---|---|
| Agent loop | `apps/api/src/agentforge/agent/loop.py`; OpenHands-inspired design documented in ADR-0001 |
| File-enabled execution | workspace manager, subprocess sandbox, upload/download routes |
| Tool scope | typed tool registry under `apps/api/src/agentforge/tools/`; phase/risk/approval metadata |
| User status | session status, progress cards, failure cards, activity panel, validation summary |
| Session state | SQLite session row plus workspace manifest and append-only `events.jsonl` |
| Resume behaviour | stable `/author/{sid}` and `/repair/{sid}` routes replay persisted session/events/artifacts; retry = full rerun |
| Budgets and limits | token/tool/step/wall-clock caps in config and session metadata; UI budget cards |
| Fault tolerance | typed failures, cannot-reproduce handling, validation failure cards, preserved artifacts |

## UX semantics (honest)

| Topic | Behaviour |
|-------|-----------|
| Expense review queue | UI triage for user decisions — does **not** mutate `outputs/output.csv` or `outputs/exceptions.csv` |
| Golden output | Validation-only independent check; not injected into codegen prompts |
| Reference samples | Prompt/context loading only; Author still requires model-authored contract/code/tests |
| Retry | Full rerun required; resume restores persisted state without partial retry |

## Local verification status

Last completed local verification from the current branch:

- Backend pytest: `309 passed, 1 deselected`
- Focused Author/eval/budget/blind-harness smoke tests: `67 passed`
- Focused Repair regressions: `8 passed`
- Frontend typecheck + build: passed (see `reports/final_repo_cleanliness_audit.md`)

**Author GREEN session:** `98942274-b4fb-4e0d-a3b8-0b8144bc01db` — Expense Exception Review, four-tier PASS including golden 7/7.

**Repair GREEN session:** `b8317f51-384a-469a-92d3-90443853a4c5` — invoice_aging_v2, 5/2 → 7/0.
