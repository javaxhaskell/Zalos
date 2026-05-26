# Repair Agent Architecture Audit

**Date:** 2026-05-25  
**Scope:** AgentForge Repair Existing Agent workflow (built-in sample: `invoice_aging_v2`)

## Thesis (INV alignment)

> The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records. The model never touches the user's workspace without recorded approval. Tools are a typed registry; sandboxes are bounded; audit is append-only. Synthetic data only.

Repair is **evidence-gated**: completion requires reproduced pytest failure, validated patch apply, post-fix all-green, and `repair_report.{md,json}` on disk (INV-1, INV-3, INV-6).

---

## 1. Frontend entry

| Item | Location |
|---|---|
| Dashboard CTA | `apps/web/app/page.tsx` → workflow picker |
| Workflow picker copy | `apps/web/src/components/workflow-picker.tsx` — "Invoice aging boundary repair" |
| Repair wizard | `apps/web/app/repair/[sid]/page.tsx` |
| Built-in sample constant | `BUILTIN_SAMPLE_AGENT.name = "invoice_aging_v2"` |
| Load sample button | `InputStage` → `loadFixture(sessionId, "invoice_aging_v2")` |
| ZIP upload | `ZipUpload` → `POST /sessions/{id}/upload_agent_zip` |
| Problem report | User pastes text or uploads `problem_report.md` |
| Start | `runSession(sessionId)` → `POST /sessions/{id}/run` |
| Status cards | `AgentSummaryCard`, `DiagnosisCard`, `PatchProposalCard`, `RepairReportCard`, `RepairSummaryCard` |
| Evidence checklist | `deriveRepairEvidence()` in repair page — before/after pytest, patch, report paths |
| Audit / downloads | `apps/web/app/sessions/[sid]/audit/page.tsx` |

Polling: `useSessionState` + events poller (2s while `running`).

---

## 2. API endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/sessions` | Create repair session (`workflow: "repair"`) |
| `POST` | `/sessions/{id}/load_fixture/{name}` | Copy bundled broken agent into `working/` |
| `POST` | `/sessions/{id}/upload_agent_zip` | Extract user ZIP into `working/` |
| `POST` | `/sessions/{id}/run` | Dispatch `RepairFlow` (202, background task) |
| `GET` | `/sessions/{id}` | Session status + manifest summary |
| `GET` | `/sessions/{id}/events` | Append-only event log (polling) |
| `POST` | `/sessions/{id}/approve` / `reject` | Approval gate (INV-3) |
| `POST` | `/sessions/{id}/finalise` | Terminal finalise (repair uses orchestrator auto-complete) |
| `GET` | `/sessions/{id}/artifacts/{path}` | Download workspace artifacts |

Router modules: `apps/api/src/agentforge/api/routers/sessions.py`, `fixtures.py`.

---

## 3. Orchestrator

| Component | Path | Role |
|---|---|---|
| `RepairFlow` | `apps/api/src/agentforge/orchestrator/repair_flow.py` | Two coarse phases: advisory LLM (INFO) + deterministic pipeline (FIX) |
| `runner.py` | `apps/api/src/agentforge/orchestrator/runner.py` | Bridges `POST /run` → `RepairFlow.run()` |
| `repair_problem.py` | Problem precedence: user text > `problem_report.md` > fixture default |
| `repair_proposal.py` | Model JSON proposal + evidence inference fallback (`<= 31` → `<= 30`) |
| `state_machine.py` | Guarded phase transitions; requires `REPRODUCTION_RESULT` before diagnose in LLM path |

### Deterministic pipeline (`_execute_repair_pipeline`)

1. Resolve `agent.py` + `tests/` under `working/`
2. Resolve primary problem statement
3. Run pytest **before fix** → persist log + `decision_input` (`pytest_before_fix`)
4. Build evidence → `resolve_repair_proposal` (model or infer)
5. `assess_problem_evidence_alignment` — fail if reported problem doesn't match failures
6. `validate_repair_proposal` — snippet uniqueness, workspace bounds, evidence linkage
7. Apply patch → `PATCH_APPLIED` + `reports/agent_py.patch`
8. Run pytest **after fix**
9. Write `repair_report.{md,json}` + completion metadata
10. Emit `WORKFLOW_COMPLETED` only if all gates pass

Covering `APPROVAL_GRANTED` events synthesized for write tools (ADR-0006).

---

## 4. Fixture paths

| Fixture | Path | UI / demo |
|---|---|---|
| **Primary (v2)** | `fixtures/broken_agents/invoice_aging_v2/` | Built-in sample in repair wizard |
| Legacy (v1) | `fixtures/broken_agents/invoice_aging_v1/` | Eval scenario R-01, older HTTP tests |
| Config root | `Settings.fixtures_broken_agents_root` → `FIXTURES_BROKEN_AGENTS_ROOT` env |

### `invoice_aging_v2` layout

```
invoice_aging_v2/
├── agent.py              # boundary bug: days_overdue <= 31
├── problem_report.md     # AR clerk symptom report
├── data/input_invoices.csv
├── data/expected_output.csv
└── tests/test_agent.py   # 7 tests: 5 pass, 2 fail before fix
```

---

## 5. Workspace layout (per session)

```
${WORKSPACES_ROOT}/${session_id}/
├── manifest.json
├── events.jsonl
├── working/              # agent under repair (flat copy from load_fixture)
│   ├── agent.py
│   ├── problem_report.md
│   ├── data/
│   └── tests/
├── evals/
│   └── expected_output.csv   # staged golden from fixture
├── reports/
│   ├── before_fix_pytest_output.txt
│   ├── after_fix_pytest_output.txt
│   ├── agent_py.patch
│   ├── repair_report.md
│   └── repair_report.json
├── archive.zip           # after build_archive()
└── SESSION_README.md
```

---

## 6. Model stages

| Stage | Mode | Cap | Notes |
|---|---|---|---|
| `repair.info` | Advisory LLM | 6 steps / 120s wall | Optional inspect; does not block completion |
| Proposal | Model or infer | — | `resolve_repair_proposal`; empty `FakeModelClient` uses inference |
| `repair.fix` | Deterministic | — | Patch apply, pytest, report — no LLM required for demo path |

Prompt: `apps/api/src/agentforge/agent/prompts/repair.md` (INFO phase system prompt).

---

## 7. Tools (repair-relevant)

Registered in `apps/api/src/tools/registry.py`:

- **Read:** `list_workspace`, `inspect_file`, `inspect_csv_schema`, repair tools (`summarise_agent_purpose`, `classify_problem`, `diagnose`, `propose_patch`, `record_reproduction`)
- **Write (approval-gated):** `apply_patch`, `run_pytest`, `run_python_script`, `validate_output`, `generate_repair_report`, `finalise_session`, `archive_workspace`

Orchestrator pipeline calls sandbox pytest directly (not always via agent loop tools).

---

## 8. Patch application

- `repair_proposal.apply_repair_proposal()` — single unique `old_snippet` → `new_snippet` replace
- Validates: workspace-relative path, `.py` only, ≤5 lines, ≤400 chars, evidence-linked
- v2 canonical fix: `if days_overdue <= 31:` → `if days_overdue <= 30:` plus comment cleanup
- Diff written to `reports/agent_py.patch`; `PATCH_APPLIED` event emitted

---

## 9. Test execution

- Subprocess via `SandboxRunner` / `_run_pytest_capture` in `repair_flow.py`
- `cwd` pinned to directory containing `agent.py`'s parent (working tree root)
- Command: `python -m pytest tests/ -v --tb=short`
- Parsed summary via `validation_tools._parse_pytest_minimal`
- Before/after logs persisted as artifacts + `decision_input` events

---

## 10. Status persistence

| Store | Content |
|---|---|
| SQLite `sessions` | `status`, budgets, workflow |
| `manifest.json` | `completion` block on success (pytest summaries, changed files, artifact paths) |
| `events.jsonl` | Append-only chain (INV-6): fixture_loaded, pytest_before/after, repair_proposal, patch_applied, repair_report_generated, workflow_completed |

Resume: `POST /run` on paused sessions; repair demo path typically completes in one run.

---

## 11. Report / archive

| Artifact | Writer |
|---|---|
| `repair_report.md` / `.json` | `RepairFlow._write_repair_report()` — 8 markdown sections |
| `SESSION_README.md` | `persistence/archive.py` — human summary incl. "How this was repaired" |
| `archive.zip` | `build_archive()` — workspace snapshot for download |

---

## 12. Test coverage map

| Test module | Focus |
|---|---|
| `test_repair_fixture_invoice_aging_v2.py` | Fixture integrity: 5/2 fail, canonical fix → 7/0 |
| `test_repair_evidence_gate.py` | Full evidence chain, cannot complete without patch |
| `test_repair_run_endpoint.py` | HTTP load_fixture + /run → completed (v1 + v2) |
| `test_repair_flow_e2e.py` | In-process scripted LLM repair (v1) |
| `test_repair_proposal.py` | Inference + validation |
| `test_repair_problem_precedence.py` | Problem report precedence |
| `test_repair_tools.py` | Repair tool handlers |
| `test_repair_validation.py` | Golden / output validation |
| `test_generate_repair_report.py` | Report tool |

**51** repair-tagged tests collected; all green as of audit date.

---

## 13. Gaps / notes

- UI demo uses **v2** (boundary bug); eval scenario **R-01** still references **v1** (date-format bug) — both supported.
- `load_fixture` copies fixture children flat into `working/` (not `working/invoice_aging_v2/`); `_resolve_repair_target` walks `rglob("agent.py")` — works for both layouts.
- Repair completion does **not** require real LLM API when inference path succeeds (FakeModelClient with empty script).
- Frontend build may fail on unrelated author-preview lint (`no-unused-vars`) — not repair-specific.
