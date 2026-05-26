# Tool Scope Audit — Author & Repair Workflows

**Date:** 2026-05-25  
**Scope:** Which tools are available for Author and Repair, when they are enabled, and who controls execution  
**Method:** Code inspection + fixture/test evidence only (no LLM runs)

## Thesis (INV alignment)

> The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records. The model never touches the user's workspace without recorded approval. Tools are a typed registry; sandboxes are bounded; audit is append-only. Synthetic data only.

Tool scope is the security boundary (INV-2). Phase filtering, approval gates (INV-3), workspace pinning (INV-5), and append-only events (INV-6) are the load-bearing controls.

---

## Phase 1 — Files inspected

### Backend (Author / Repair / tools / sandbox / validation)

| Area | Path | Role |
|---|---|---|
| Tool registry | `apps/api/src/agentforge/tools/registry.py` | Sole dispatch surface; `list_for_phase()` |
| Tool definitions | `apps/api/src/agentforge/tools/*.py` | Per-tool risk, approval, phases, handlers |
| Agent loop | `apps/api/src/agentforge/agent/loop.py` | Phase filter, approval gate, idempotency, dispatch |
| System prompts | `apps/api/src/agentforge/agent/prompts/author.md`, `repair.md` | Model-visible tool lists per phase |
| Author orchestrator | `apps/api/src/agentforge/orchestrator/author_flow.py` | INFO/BUILD loops; covering grants; custom pipeline routing |
| Author LLM pipeline | `apps/api/src/agentforge/orchestrator/author_llm_authoring.py`, `author_custom_build.py` | Model stages; direct file writes + `SandboxRunner` (bypass tools) |
| Repair orchestrator | `apps/api/src/agentforge/orchestrator/repair_flow.py` | Advisory INFO loop; deterministic FIX pipeline |
| Repair helpers | `repair_proposal.py`, `repair_problem.py` | Patch apply, evidence, validation (orchestrator-direct) |
| Sandbox | `apps/api/src/agentforge/sandbox/runner.py` | Subprocess cwd pin, timeout, env strip, output cap |
| State machine | `apps/api/src/agentforge/orchestrator/state_machine.py` | Phase transitions; repair diagnose prerequisites |
| Config / budgets | `apps/api/src/agentforge/config.py` | Step/token/wall/tool-call caps |
| Contracts | `CONTRACTS.md` §4, `docs/adr/0005-tool-registry.md`, `docs/adr/0006-approval-model.md` | Documented 20-tool contract |

### Frontend (tool / approval / audit visibility)

| Component | Path | Role |
|---|---|---|
| Author wizard | `apps/web/app/author/[sid]/page.tsx` | Approval panel wiring, event polling |
| Repair wizard | `apps/web/app/repair/[sid]/page.tsx` | Same + repair evidence checklist |
| Approval UI | `apps/web/src/components/approval-panel.tsx` | POST `/approve` / `/reject` only (INV-3) |
| Activity / audit | `activity-panel.tsx`, `event-log-table.tsx`, `technical-audit-section.tsx` | Milestones + expandable raw events |
| UX language | `apps/web/src/lib/ux-language.ts` | Humanises `tool_invoked`, `approval_*`, phases |
| Budget UI | `budget-banner.tsx` | Surfaces `tool_calls_used` / limits |

### Test / fixture evidence (no LLM runs)

| Test / fixture | Evidence for |
|---|---|
| `apps/api/tests/test_tool_registry.py` | Registered tool set, phase exposure, ADR override on auto-approved writes |
| `apps/api/tests/test_agent_loop.py` | Phase rejection, approval pause, advisory mode, ask_user pause |
| `apps/api/tests/test_code_tools.py`, `test_execution_tools.py` | Write/execution tools require approval |
| `apps/api/tests/test_template_tools.py` | `seed_template` **not** registered |
| `apps/api/tests/test_archive.py` | `archive_workspace` phase exposure |
| `apps/api/tests/test_repair_evidence_gate.py` | Repair completion via deterministic pipeline, not FIX loop |
| `fixtures/broken_agents/invoice_aging_v2/` | Repair demo fixture |
| `templates/bank_categoriser/data/sample_input.csv` | Author upload sample |

---

## Phase 2 — Stage tables

### Author workflow

| Stage | Tool / capability | Model-controlled? | Backend-controlled? | Inputs | Outputs | Safety boundary |
|---|---|---|---|---|---|---|
| **Pre-INFO gate** | Intent/schema assessment (`assess_author_pre_pipeline`) | No — orchestrator | Yes — `author_flow._try_pre_author_validation_gate` | Upload header, user description | Route to custom pipeline, stop, or continue INFO loop | Fails closed on intent mismatch |
| **`author.info`** (agent loop) | `list_workspace`, `inspect_file`, `inspect_csv_schema`, `inspect_xlsx_schema`, `ask_user` | Model proposes calls | Backend filters via `registry.list_for_phase(AUTHOR_INFO)`; loop dispatches | Workspace-relative paths, file IDs | Profiles, file contents, `ask_user` question | Read-only; path pin via `resolve_in`; `ask_user` pauses loop |
| **Custom build pipeline** (primary path) | Model stages: contract_planning, contract_review, code_generation, test_generation | Model text/JSON | Orchestrator writes files (`path.write_text`), runs `SandboxRunner` pytest, `validate_against_contract` | Upload CSV/XLSX, user description | `generated/*`, reports, validation | **Does not go through tool registry** for writes/runs; static safety scan + artifact gates |
| **INFO → BUILD transition** | Phase transition + covering `APPROVAL_GRANTED` | No | Yes — `state_machine.transition`; `_record_covering_grants` for 6 tools | Prior INFO completion | Session-scoped grants for write/run/finalise tools | Synthesised system grants (ADR-0006 prototype shortcut) |
| **`author.build`** (agent loop, fallback path) | `list_workspace`, `inspect_file`, `write_file`, `apply_patch`, `run_python_script`, `run_pytest`, `validate_output`, `generate_validation_report`, `finalise_session`, `archive_workspace`, `ask_user` | Model proposes | Backend phase filter + approval + handlers | Typed Pydantic args | Files, execution obs, validation report, manifest complete | Approval gate; sandbox for run/patch; `finalise_session` requires `ARTIFACT_GENERATED` |
| **Completion gate** | `enforce_author_completion_gate` | No | Yes — checks model provenance artifacts | Event log + workspace files | `WORKFLOW_COMPLETED` or typed failure | Blocks completion without model-authored contract/code/tests |

### Repair workflow

| Stage | Tool / capability | Model-controlled? | Backend-controlled? | Inputs | Outputs | Safety boundary |
|---|---|---|---|---|---|---|
| **Pre-INFO grants** | Covering `APPROVAL_GRANTED` for 6 FIX tools | No | Yes — emitted before INFO loop | Session start | Session-scoped grants | Same ADR-0006 shortcut as Author |
| **`repair.info`** (advisory loop, max 6 steps / 120s) | `list_workspace`, `inspect_file`, `inspect_csv/xlsx`, repair read tools, `run_pytest`, `run_python_script`, `ask_user` | Model proposes (optional) | Backend phase filter; advisory mode downgrades budget failures | Agent files, problem text | Typed repair events (`AGENT_SUMMARY_PRODUCED`, `REPRODUCTION_RESULT`, etc.) | **Non-blocking** — pipeline proceeds regardless |
| **Deterministic FIX pipeline** | `_execute_repair_pipeline`: pytest before/after, `validate_repair_proposal`, `apply_repair_proposal`, report write | Model proposal optional (`resolve_repair_proposal`) | Yes — orchestrator + `SandboxRunner` | `working/agent.py`, tests, problem report | Patch, pytest logs, `repair_report.{md,json}` | Evidence gates; unsafe proposals rejected; **no agent loop in `repair.fix`** |
| **`repair.fix`** (registry only — loop not invoked) | Tools registered: `apply_patch`, `run_*`, `generate_repair_report`, `finalise_session`, `archive_workspace`, … | N/A in current code | Phase exists in registry/prompts only | — | — | Prompt/contract drift vs runtime |

### Registered tool inventory (runtime snapshot)

**19 tools** in `build_registry()` (CONTRACTS.md documents **20** — `seed_template` absent).

| Phase | Count | Tools |
|---|---:|---|
| `author.info` | 5 | `ask_user`, `inspect_csv_schema`, `inspect_file`, `inspect_xlsx_schema`, `list_workspace` |
| `author.build` | 11 | above minus csv/xlsx inspectors, plus `apply_patch`, `archive_workspace`, `finalise_session`, `generate_validation_report`, `run_pytest`, `run_python_script`, `validate_output`, `write_file` |
| `repair.info` | 12 | workspace + csv/xlsx + all repair read tools + `run_pytest` + `run_python_script` + `ask_user` |
| `repair.fix` | 10 | `apply_patch`, `archive_workspace`, `ask_user`, `finalise_session`, `generate_repair_report`, `inspect_file`, `list_workspace`, `run_pytest`, `run_python_script`, `write_file` |

**Approval matrix (registered tools)**

| Tool | Risk | `requires_approval` | ADR auto-approve | Phases |
|---|---|---|---|---|
| Read/inspect tools | read | false | — | per table above |
| `write_file`, `apply_patch` | low_write | true | — | author.build, repair.fix |
| `run_python_script`, `run_pytest` | high_write | true | — | author.build, repair.info, repair.fix |
| `archive_workspace`, `finalise_session` | low_write | true | — | author.build, repair.fix |
| `generate_validation_report`, `generate_repair_report` | low_write | false | ADR-0006 | author.build / repair.fix |
| Repair typed tools (`summarise_*`, `diagnose`, …) | read | false | — | repair.info |

---

## Phase 3 — Answers to 17 tool-scope questions

### 1. What is the only execution path for registered side effects?

The agent loop (`loop.py`) dispatches only through `ToolRegistry.get()` → handler. Unregistered names and wrong-phase tools are rejected before dispatch (`test_agent_loop.py::test_loop_rejects_tool_not_in_current_phase`).

**Caveat:** Load-bearing Author and Repair completion paths also mutate workspace via orchestrator-direct I/O (`author_llm_authoring.py`, `author_custom_build.py`, `repair_flow._execute_repair_pipeline`) and `SandboxRunner` — outside the tool registry.

### 2. How many tools are registered vs documented?

**Registered:** 19 (`test_tool_registry.py::test_build_registry_ships_all_currently_registered_tools`).  
**Documented:** 20 in `CONTRACTS.md` §4.  
**Missing:** `seed_template` — explicitly tested as absent (`test_template_tools.py`).

### 3. Which tools does the model see in each Author phase?

Exactly the output of `registry.list_for_phase()` for `author.info` (5 tools) and `author.build` (11 tools). System prompt in `author.md` matches except it omits `archive_workspace` from the prose list.

### 4. Which tools does the model see in each Repair phase?

Registry exposes 12 tools in `repair.info` and 10 in `repair.fix` (see table). System prompt `repair.md` lists `validate_output` in FIX — **not registered for `repair.fix`** (registry gap).

### 5. When does the backend switch coarse tool phases?

| Workflow | Transition | Controller |
|---|---|---|
| Author | `author.info` → `author.build` | `AuthorFlow.run` after INFO loop completes without pause/error; `state_machine.transition` to `AUTHOR_GENERATE` |
| Author (custom) | INFO loop may run briefly, then `_try_pre_author_validation_gate` routes to `execute_custom_workflow_pipeline` **before** BUILD loop | Orchestrator |
| Repair | `repair.info` → deterministic pipeline | After advisory INFO loop; **no** `agent_loop.run(..., REPAIR_FIX)` call |

Phase enum: `ToolPhase` in `schemas/common.py` (`author.info`, `author.build`, `repair.info`, `repair.fix`).

### 6. Who controls which tools appear in each model API call?

**Backend-controlled.** Each loop iteration calls `registry.list_for_phase(phase)`; the model cannot expand the set (INV-2).

### 7. Which tools require approval before dispatch?

All `requires_approval=True` tools: `write_file`, `apply_patch`, `run_python_script`, `run_pytest`, `archive_workspace`, `finalise_session`. Verified in `test_code_tools.py`, `test_execution_tools.py`, `test_archive.py`.

Auto-approved writes (ADR-0006 cited): `generate_validation_report`, `generate_repair_report` (`test_build_registry_write_tools_cite_adr_when_auto_approved`).

### 8. How do covering session grants interact with per-step approval?

Orchestrators emit `APPROVAL_GRANTED` with `payload.scope="session"` and `payload.tool_name=<tool>` (`author_flow._record_covering_grants`, `repair_flow` pre-INFO). `AgentLoop._has_approval` honours covering grants for any step (`loop.py` L673–704).

**Effect:** In demo runs, write/run tools proceed without user clicks despite ADR-0006 describing per-gate UX. Gate logic remains enforced — grants are synthesised, not bypassed.

### 9. Does the UI actually gate tool execution?

Yes when `APPROVAL_REQUESTED` fires and session is `paused_approval`. `ApprovalPanel` POSTs to `/sessions/{id}/approve` (INV-3). In current happy paths, covering grants prevent the panel from appearing for write/run tools.

### 10. How are patch and file writes bounded?

- Path discipline: `WorkspaceManager.resolve_in` rejects traversal (`code_tools.py`).
- Overwrite guard on `write_file` (`overwrite=False` default).
- Generated file count budget (`budget_generated_file_count=20`).
- Patch via sandbox `patch -u -p1` with stderr surfaced.
- Repair patch additionally validated by `validate_repair_proposal` before orchestrator apply.

### 11. How does the sandbox constrain execution tools?

`SandboxRunner`: cwd pinned under session workspace, minimal env (no API keys), timeouts (60s script / 120s pytest), 1 MiB stdout/stderr cap with spill to `outputs/_logs/` (`runner.py` docstring). Used by execution tools and orchestrator pipelines.

### 12. Does the primary Author path use tools for codegen?

**No.** Custom/LLM-first Author (`execute_custom_workflow_pipeline`) writes `generated/agent.py`, tests, and reports via orchestrator `write_text` and runs pytest through `SandboxRunner` directly (`author_custom_build.py`). The INFO loop may use inspect tools; BUILD loop is skipped when custom gate fires.

### 13. Does the load-bearing Repair path use tools for patch/validate/report?

**No.** `RepairFlow._execute_repair_pipeline` runs pytest, applies patch, writes reports via Python helpers — not `apply_patch` / `generate_repair_report` tools. Advisory INFO loop may call repair read tools and `run_pytest`, but completion does not depend on them (`test_repair_evidence_gate.py`).

### 14. What audit trail exists for tool calls?

Append-only `events.jsonl`: `TOOL_INVOKED`, `TOOL_OBSERVED`, plus domain events (`FILE_WRITTEN`, `PATCH_APPLIED`, `EXECUTION_STARTED`, repair typed events). Orchestrator-direct mutations emit their own kinds (`MODEL_CALLED`, `ARTIFACT_GENERATED`, `WORKFLOW_COMPLETED`, etc.) but may omit `TOOL_INVOKED`.

### 15. How does the finance UI expose tool scope?

- Milestone labels via `ActivityPanel` / `ux-language.ts` (e.g. “Running a step”, “Waiting for approval”).
- Workflow timeline and status cards — phase-oriented, not tool-registry-oriented.
- Budget banner shows aggregate tool-call usage, not per-tool policy.
- No screen lists “tools enabled in this phase.”

### 16. How does the engineering UI expose tool detail?

- Expandable full event log (`EventLogTable`) with raw JSON and `tool_name` in technical detail.
- Audit export route (`/sessions/{sid}/audit`).
- `ApprovalPanel` shows `toolName` and optional diff (secondary).

### 17. What budget limits constrain tool usage?

| Limit | Value | Source |
|---|---|---|
| Author loop steps | 25 | `budget_steps_author` |
| Repair loop steps | 20 (advisory INFO capped at 6) | `budget_steps_repair`, `repair_flow._ADVISORY_STEP_CAP` |
| Tool calls | 40 | `budget_tool_calls` |
| Tokens | 150k | `budget_tokens` |
| Wall clock | 1500s (advisory repair INFO min 120s) | `budget_wall_seconds` |
| Validation re-prompts | 3 consecutive failures | `loop.py` docstring |

**Gap:** Thesis INV-12 “same-tool-same-args circuit breaker” — no implementation found (`grep circuit` → none).

---

## Phase 4 — GREEN / AMBER / RED gap table

| Area | Status | Evidence | Gap |
|---|---|---|---|
| **Registry as sole loop dispatch** | GREEN | `loop.py` INV-2 docstring; phase rejection test | — |
| **Phase enablement metadata** | GREEN | `ToolDefinition.phases`; `list_for_phase` tests | — |
| **Phase enablement at runtime (Repair FIX)** | RED | `repair_flow.py` never runs FIX loop | `repair.fix` tools registered but unused on completion path |
| **Phase enablement at runtime (Author build)** | AMBER | Custom pipeline skips BUILD loop | Tool registry secondary to orchestrator I/O on primary path |
| **Model vs backend authority (tool list)** | GREEN | Backend filters every model turn | — |
| **Model vs backend authority (mutations)** | AMBER | Orchestrator writes files directly | Still backend-controlled, but bypasses tool registry / approval semantics for codegen |
| **Approval gate in executor** | GREEN | `requires_approval` check in loop | — |
| **Approval UX (user clicks)** | AMBER | Covering system grants in both flows | ADR-0006 per-gate UX not exercised on happy path |
| **Sandbox isolation** | GREEN | `SandboxRunner` cwd/env/timeout | — |
| **Patch/write safety** | GREEN | Path pin, proposal validation (repair), overwrite guard | — |
| **Audit trail (tool calls)** | GREEN (orchestrator) / AMBER (uniformity) | `TOOL_*` when loop dispatches; `tool_action_recorded` on orchestrator steps (2026-05-25) | See `docs/TOOL_SCOPE_MODEL.md` and `reports/tool_scope_alignment_implementation.md` |
| **Finance visibility** | AMBER | Milestones, budget banner | No phase/tool capability summary for finance users |
| **Engineering visibility** | GREEN | Raw event log, audit export, tool names in payloads | — |
| **Contract vs code (tool count)** | AMBER | 19 vs 20 tools | `seed_template` documented but unregistered (intentional per test) |
| **Contract vs code (repair FIX)** | RED | `repair.md` lists `validate_output` | Not in `repair.fix` registry; FIX loop not run |
| **Idempotency (INV-7)** | AMBER | Loop derives key internally | Tool input schemas do not expose `idempotency_key` field (thesis checklist drift) |
| **Circuit breaker (INV-12)** | RED | Not implemented | Only step/token/wall/validation caps |

---

## Phase 5 — Summary

| Item | Value |
|---|---|
| **Report path** | `reports/tool_scope_audit.md` |
| **Overall status** | **AMBER** — registry, phase metadata, sandbox, and loop dispatch are solid; load-bearing Author/Repair paths bypass the tool loop; Repair FIX phase is registry-only; approval UX is collapsed to system grants on happy paths |

### Strongest evidence

1. **`build_registry()` + `test_tool_registry.py`** — explicit 19-tool inventory, phase exposure, ADR citations on auto-approved writes.
2. **`AgentLoop._dispatch_one`** — phase filter, Pydantic validation, approval gate, idempotency cache before handler (`loop.py` L411–579).
3. **`test_agent_loop.py`** — phase rejection, approval pause, advisory downgrade for repair INFO.
4. **`test_repair_evidence_gate.py`** — repair completion through deterministic pipeline with real artifacts on `invoice_aging_v2`.
5. **`SandboxRunner` docstring + `test_execution_tools.py`** — bounded subprocess execution for run tools.

### Weakest gaps

1. **Repair FIX agent loop never runs** — registered tools and `repair.md` describe a model-driven FIX phase that the orchestrator replaces with `_execute_repair_pipeline`.
2. **Author primary path bypasses write/run tools** — `author_custom_build` / `author_llm_authoring` mutate workspace without `write_file` / `run_pytest` tool dispatch or `TOOL_INVOKED` parity.
3. **Covering system approval grants** — gate exists but user-facing ADR-0006 approvals are not exercised on demo happy paths.
4. **Prompt/registry drift** — `validate_output` advertised in `repair.md` FIX section but absent from `repair.fix` phase list.
5. **INV-12 circuit breaker** — documented in thesis, not implemented.

### Minimal fix (smallest diff toward GREEN submission narrative)

1. **Document runtime truth in README / CONTRACTS** — state that Author completion and Repair completion are orchestrator-gated pipelines; the agent loop + registry are the bounded interface for inspect/advisory turns, not the sole mutation path. *(Docs-only, aligns grader expectations.)*
2. **Align `repair.md` with registry** — remove `validate_output` from FIX tool list *or* add it to `repair.fix` phases if FIX loop is ever wired. *(One-line registry or prompt edit.)*
3. ~~**Emit synthetic `TOOL_INVOKED`-equivalent or domain events**~~ **Done (2026-05-25):** `tool_action_recorded` on `DECISION_INPUT` via `tool_scope_audit.py` — honest metadata, not fake `TOOL_INVOKED`.

Optional (not minimal): wire `repair.fix` agent loop for patch apply *or* delete unused FIX tools from registry to eliminate dead surface.

### Submission-ready statement

AgentForge implements a **typed 19-tool registry** with **coarse phase scoping** (`author.info` / `author.build` / `repair.info` / `repair.fix`), **approval-gated write and execution tools**, and a **bounded subprocess sandbox**. The **agent loop** is the only path for **model-proposed tool calls** and enforces phase, schema, approval, and idempotency checks. **Load-bearing Author and Repair completion** additionally use **orchestrator-controlled pipelines** (model stages + deterministic evidence gates) that write files and run pytest **outside** the tool loop but **inside** the same workspace and audit conventions. Operators should treat **`test_tool_registry.py`**, **`test_agent_loop.py`**, and **`test_repair_evidence_gate.py`** as the primary evidence bundle; **`events.jsonl`** on a demo session shows inspect-tool activity in INFO and pipeline events on completion.

---

## Appendix — Key code references

Registry phase filter:

```129:143:apps/api/src/agentforge/tools/registry.py
    def list_for_phase(self, phase: ToolPhase) -> list[dict[str, Any]]:
        """Return the Anthropic tool-use shape for tools enabled in ``phase``."""
        result: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if phase in tool.definition.phases:
                result.append(_anthropic_shape(tool))
        result.sort(key=lambda t: t["name"])
        return result
```

Loop phase rejection:

```436:453:apps/api/src/agentforge/agent/loop.py
        if phase not in tool.definition.phases:
            ...
            return obs_factory.not_registered(
                tool_name=use.name,
                available=[
                    name
                    for name in self.registry.all_names()
                    if phase in self.registry.get(name).definition.phases
                ],
            )
```

Author covering grants (BUILD):

```57:72:apps/api/src/agentforge/orchestrator/author_flow.py
_RUN_CONSENT_TOOLS: tuple[str, ...] = (
    "run_python_script",
    "run_pytest",
)
_BUILD_COVERING_TOOLS: tuple[str, ...] = _RUN_CONSENT_TOOLS + (
    "write_file",
    "apply_patch",
    "generate_validation_report",
    "finalise_session",
)
```

Repair: advisory INFO only, then deterministic pipeline (no FIX loop):

```226:260:apps/api/src/agentforge/orchestrator/repair_flow.py
        info_outcome = await self.agent_loop.run(
            session_id=session_id,
            phase=ToolPhase.REPAIR_INFO,
            ...
            advisory_mode=True,
        )
        ...
        pipeline_status = await self._execute_repair_pipeline(
            session_id=session_id,
            step=info_outcome.steps_taken,
            stage_label="repair_evidence_pipeline",
        )
```
