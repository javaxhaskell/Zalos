# Tool scope alignment — implementation report

**Date:** 2026-05-25  
**Goal:** Lightweight orchestrated tool-action audit without rewriting Author/Repair pipelines.

## What changed

| File | Change |
|------|--------|
| `apps/api/src/agentforge/orchestrator/tool_scope_audit.py` | New helper: `record_tool_action`, `record_model_orchestrated_call`, `tool_action_events` |
| `apps/api/src/agentforge/orchestrator/author_llm_authoring.py` | Model stage audit in `_call_model`; static safety scan audit |
| `apps/api/src/agentforge/orchestrator/author_custom_build.py` | Inspect/profile, generated agent run, pytest, validation, archive audit |
| `apps/api/src/agentforge/orchestrator/repair_flow.py` | Workspace discovery, inspect, pytest before/after, proposal, patch, repair report audit |
| `docs/TOOL_SCOPE_MODEL.md` | Two-layer tool scope documentation |
| `apps/api/tests/test_tool_scope_audit_events.py` | Regression tests for audit payloads |
| `reports/tool_scope_audit.md` | Status note (implementation landed) |

No new `EventKind` enum value — uses existing `DECISION_INPUT` with `payload.kind = "tool_action_recorded"` to avoid ADR churn while staying honest (not fake `TOOL_INVOKED`).

## Events added (orchestrator path)

### Author (`workflow=author`, `phase=author.build`, `dispatch_mode=orchestrator`)

| `tool_name` | `controlled_by` | When |
|-------------|-----------------|------|
| `inspect_file` / `inspect_csv_schema` | backend | Upload profile + ingest |
| `contract_planning`, `contract_review`, `code_generation`, `test_generation`, `schema_repair`, … | model | Each `_call_model` purpose (started/completed/failed) |
| `static_safety_scan` | backend | Pre-execution safety check on generated files |
| `safety_scan` | model | Optional `safety_repair` model stage (via purpose map) |
| `generated_agent_execution` | generated_code | Sandbox run of `generated/agent.py` |
| `generated_pytest` | backend | Orchestrator pytest via `SandboxRunner` |
| `deterministic_validation` | backend | Contract validation publish |
| `archive_generation` | backend | Success and failure-path `build_archive` |

### Repair (`workflow=repair`, `phase=repair.fix`, `dispatch_mode=orchestrator`)

| `tool_name` | `controlled_by` | When |
|-------------|-----------------|------|
| `list_workspace` | backend | Resolve `working/` agent + tests |
| `inspect_file` | backend | Files inspected for evidence |
| `pytest_before_fix` / `pytest_after_fix` | backend | Evidence-gated pytest |
| `repair_proposal` | model | `resolve_repair_proposal` |
| `apply_patch` | backend | Validated patch application |
| `generate_repair_report` | backend | `repair_report.{md,json}` |

## AgentLoop vs orchestrator

| Path | `dispatch_mode` | Audit event |
|------|-----------------|-------------|
| Model tool_use in loop | `agent_loop` | `TOOL_INVOKED` / `TOOL_OBSERVED` |
| Custom Author build + Repair FIX pipeline | `orchestrator` | `tool_action_recorded` on `DECISION_INPUT` |

Demo Author/Repair completion remains on the orchestrator path; INFO loops may still emit `TOOL_INVOKED` when the model uses inspect tools.

## Honest against brief

| Requirement | Status |
|-------------|--------|
| No pipeline rewrite | Yes — append-only audit calls only |
| No fake tool calls | Yes — no synthetic `TOOL_INVOKED` |
| No claim of model direct tool access for backend steps | Yes — `controlled_by` + `dispatch_mode` explicit |
| Validation not weakened | Yes — gates unchanged |
| Working demos preserved | Yes — same control flow |

## Limitations

- Not every orchestrator helper has paired `started`/`completed` (minimal coverage on stable steps).
- Failure mitigation guidance (`build_failure_mitigation`) is still computed on session GET, not duplicated as audit events.
- Repair completion does not auto-archive in `repair_flow` (archive remains user/API-driven).
- `repair.info` advisory loop tool calls still audit only via layer 1 when used.

## Tests

See `apps/api/tests/test_tool_scope_audit_events.py` and the command bundle in the task prompt.
