# Architecture and design

AgentForge is a local prototype for finance teams to author new Python agents from sample CSV/XLSX files and repair existing Python agents from a problem report. The system is designed around one load-bearing thesis:

> The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records.

## User-facing shape

- `/` shows the two primary workflows: **Author a new agent** and **Repair an existing agent**.
- `/author/{session_id}` accepts a finance workflow description and CSV/XLSX uploads, then shows progress, questions, failures, output previews, validation, model provenance, artifacts, and technical audit.
- `/repair/{session_id}` accepts a ZIP or bundled broken fixture plus a problem report, then shows reproduction, diagnosis, patch, validation, report, artifacts, and audit.

The UI is intentionally finance-user first. Technical evidence remains available, but it is secondary to status, outputs, reports, warnings, and downloads.

## Backend components

| Component | Role |
|---|---|
| FastAPI routers | Sessions, files, fixtures, approvals, artifact download, audit, evals, health |
| Agent loop | Bounded action/observation loop inspired by OpenHands CodeAct patterns |
| Orchestrators | `AuthorFlow` and `RepairFlow` coordinate workflow-specific state machines |
| Tool registry | Typed tools for inspect, write, patch, run, test, validate, report, archive |
| Sandbox runner | Subprocess execution with cwd pinning, timeout, and no external finance APIs |
| Persistence | SQLite session row, per-session workspace, append-only `events.jsonl`, manifest |
| Validation | Author contract interpreter plus Repair evidence gates |
| Archive | `archive.zip` with uploads, generated files, outputs, reports, manifest, event log, and session README |

## Author architecture

The Author workflow is LLM-first.

1. User uploads CSV/XLSX and describes the finance workflow.
2. Backend profiles the uploaded file: format, sheets, columns, row count, sample rows, nulls, candidate IDs/dates/amounts/status fields.
3. Model authors an `AuthorOutputContract`.
4. Model separately reviews the contract.
5. Model authors `generated/agent.py`.
6. Model authors `generated/tests/test_agent.py` or equivalent checks.
7. Backend safety-scans generated code.
8. Backend executes generated code in the session workspace.
9. Backend runs generated tests/checks.
10. Backend validates outputs against universal checks and model-contract-defined checks.
11. Backend records provenance and packages the archive.

Successful completion requires model-authored contract, reviewed contract, code, tests/checks, execution evidence, validation evidence, reports, manifest, event log, session README, and archive.

Templates and reference samples are prompt/input context only. They are not copied as final Author artifacts.

## Repair architecture

The Repair workflow is evidence-gated:

1. User uploads an agent ZIP or loads a bundled broken fixture.
2. Backend stages files into the session workspace.
3. System records the user problem report.
4. Tools inspect files and dependencies.
5. The workflow reproduces the issue when possible, or reports cannot-reproduce honestly.
6. A targeted patch is proposed and applied only when evidence supports it.
7. Tests or sample execution run after the fix.
8. Repair report records changed files, before/after evidence, root cause, risks, and next steps.
9. Archive packages reports, patch artifacts, manifest, event log, and workspace evidence.

Unknown or mismatched repair reports do not complete as success without evidence.

## Workspace and artifacts

Each session owns a workspace under `.workspaces/<session-id>/`.

Typical Author archive contents:

- `uploads/`
- `generated/model_contract_plan.json`
- `generated/model_contract_review.json`
- `generated/model_code_plan.json`
- `generated/agent.py`
- `generated/tests/test_agent.py`
- `outputs/`
- `reports/model_authoring_summary.md`
- `reports/validation_report.md`
- `manifest.json`
- `events.jsonl`
- `SESSION_README.md`
- `archive.zip`

Typical Repair archive contents:

- staged `working/` agent files
- patch artifacts
- before/after pytest output
- `reports/repair_report.md`
- `reports/repair_report.json`
- `manifest.json`
- `events.jsonl`
- `SESSION_README.md`
- `archive.zip`

## Audit, budgets, and resume

- Every important action is appended to `events.jsonl`.
- Mutating and execution tools are scoped by phase, risk, and approval state.
- Sessions track status, terminal errors, budget usage, and completion metadata.
- Budget limits cover model tokens, tool calls, loop steps, and wall-clock runtime.
- Returning to `/author/{session_id}` or `/repair/{session_id}` reconstructs state from the persisted session, event log, manifest, and artifacts.

## Fault tolerance

The product fails closed:

- malformed uploads fail with typed errors
- model JSON/schema errors trigger bounded model repair, then fail honestly if unrepaired
- unsafe generated code is rejected or sent back to the model
- generated-code/test failures trigger bounded model repair, then fail honestly
- contract-driven validation failures stop Author completion
- repair cannot-reproduce cases preserve diagnostics instead of claiming a fix
- budget exhaustion preserves the workspace and audit trail

## Scope boundaries

- Synthetic data only.
- No real banking, accounting, payment, billing, or ERP APIs.
- Local subprocess sandbox only; Docker-per-session is a production extension.
- Fake model tests prove orchestration, not real model capability.
- Real-model blind Author success is not claimed on this branch. Default local model is now `qwen2.5-coder:14b`; a blind re-run is pending and has not passed yet. The prior `qwen2.5:7b` blind expense run failed honestly at code generation.
