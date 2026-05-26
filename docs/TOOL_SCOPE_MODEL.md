# Tool scope model — AgentLoop registry and orchestrator capabilities

## Thesis alignment

> The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records.

Tool scope has **two audited layers**. Neither layer lets the model mutate workspace state without recorded backend control.

## Layer 1 — AgentLoop tool registry

- **Dispatch:** `agent_loop` — model proposes `tool_use` blocks; the executor validates phase, schema, approval, and idempotency, then dispatches registered handlers.
- **Surface:** `apps/api/src/agentforge/tools/registry.py` — read/write/execution tools scoped per coarse phase (`author.info`, `author.build`, `repair.info`, `repair.fix`).
- **Audit:** `TOOL_INVOKED` / `TOOL_OBSERVED` events (real registry invocations only).
- **Model role:** proposes calls; **backend** executes after gates.

Used on advisory Author INFO / Repair INFO loops and fallback Author BUILD paths. Not every successful demo completion goes through this loop.

## Layer 2 — Backend orchestration capabilities

- **Dispatch:** `orchestrator` — deterministic pipeline steps and bounded model stages invoked directly by `author_custom_build`, `author_llm_authoring`, and `repair_flow`.
- **Surface:** same conceptual capabilities as registry tools (inspect, pytest, patch, validate, archive) but implemented as orchestrator functions and `SandboxRunner`, not `ToolRegistry.dispatch`.
- **Audit:** `DECISION_INPUT` events with `payload.kind = "tool_action_recorded"`, including:
  - `tool_name` — capability label (e.g. `contract_planning`, `generated_pytest`)
  - `workflow` — `author` | `repair`
  - `phase` — e.g. `author.build`, `repair.fix`
  - `controlled_by` — `model` | `backend` | `generated_code`
  - `dispatch_mode` — `orchestrator` (this layer) or `agent_loop` (layer 1)
  - `status` — `started` | `completed` | `failed`
  - `approval_policy` — typically `auto_approved_backend_validation` for orchestrator validation/execution steps

These events are **not** `TOOL_INVOKED` — they do not claim the model called a registered tool.

## Division of responsibility

| Concern | Model | Backend |
|--------|-------|---------|
| Output contract, code, tests, repair proposals | Authors typed JSON / text via bounded model stages | Validates, persists, runs safety scan |
| File writes to `generated/`, `outputs/` | Proposes content in model payloads | Orchestrator writes bytes; generated agent runs in sandbox |
| Pytest / script execution | — | `SandboxRunner` with cwd pin and timeouts |
| Deterministic validation | — | Contract-driven validation engine |
| Archive / manifest / events.jsonl | — | Reserved paths; generated code cannot write them |

## Generated code boundary

Generated `agent.py` runs under `SandboxRunner` with workspace cwd pinned. Static safety scan rejects writes to orchestration/audit paths (`events.jsonl`, `manifest.json`, `archive.zip`, etc.) before execution.

## Demo-critical paths

Bank-categoriser Author and invoice-aging Repair completions use the **orchestrator** pipeline (layer 2). Layer 1 remains available for exploration and fallback but is not required for those happy paths. Both layers are stage-scoped and append-only audited (`events.jsonl`).

## Implementation reference

- Audit helper: `apps/api/src/agentforge/orchestrator/tool_scope_audit.py`
- Author wiring: `author_custom_build.py`, `author_llm_authoring.py`
- Repair wiring: `repair_flow.py`
- Alignment report: `reports/tool_scope_alignment_implementation.md`
