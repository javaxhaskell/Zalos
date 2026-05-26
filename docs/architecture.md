# Architecture overview

This document maps module boundaries and data flow. The canonical component diagram, state machine tables, budgets, and failure catalogue live in [`ARCHITECTURE.md`](../ARCHITECTURE.md). Operator-oriented narrative is in [`ARCHITECTURE_AND_DESIGN.md`](./ARCHITECTURE_AND_DESIGN.md).

## Thesis

The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records. The model never touches the user's workspace without recorded approval. Tools are a typed registry; sandboxes are bounded; audit is append-only. Synthetic data only.

## Layer map

```
UI (apps/web)
  → REST routers (apps/api/src/agentforge/api)
    → SessionStore + EventLog (persistence/)
      → Orchestrator (orchestrator/runner.py → author_custom_build.py | repair_flow.py)
        → Model authoring (orchestrator/author_llm_authoring.py)
        → AgentLoop + ToolRegistry (agent/, tools/)
          → SandboxRunner (sandbox/)
        → Validation engine (validation/, orchestrator/author_contract_validation.py)
      → Artifacts (workspace/, archive.py)
  → Config (config.py)
```

| Layer | Owns | Must not |
|---|---|---|
| UI | Polling, finance-friendly status, download links | Business logic, direct workspace access |
| API routers | HTTP validation, idempotency, run/cancel gates | Tool dispatch, model calls |
| SessionStore | SQLite row, manifest sync, terminal status | Append to events.jsonl except via EventLog |
| Orchestrator | Workflow sequencing, covering approvals, phase transitions | Unbounded model loops |
| Model authoring | Contract/code/test generation prompts | Direct file writes without validation |
| AgentLoop | Bounded ReAct, tool exposure by phase | Registry bypass |
| Tool registry | Typed dispatch, approval gate, idempotency | Ad-hoc subprocess spawn |
| Sandbox | Subprocess cwd pin, timeout, output cap | Host secret inheritance |
| Validation | Four-tier checks, golden diff | Golden in codegen prompts |
| EventLog | Append-only events.jsonl | In-place edits |

## Model routing and token discipline

Stage-specific models are configured in `apps/api/src/agentforge/config.py`:

| Stage | Setting | Typical use |
|---|---|---|
| Contract planning | `author_planning_model` | Schema-heavy JSON authoring |
| Contract review | `author_review_model` | Contract critique pass |
| Code generation | `author_codegen_model` | `generated/agent.py` |
| Test generation | `author_testgen_model` | `generated/tests/test_agent.py` |
| Execution repair | `author_repair_model` | Bounded repair after runtime/pytest/contract failures |

Small workflows (row count ≤ `author_small_workflow_row_threshold`) use compact contract prompts when `author_compact_contract_prompts=true`. Repair attempts are capped by `author_max_repair_attempts` (default 1).

Session budgets (tokens, steps, wall time, tool calls, generated file count) are enforced in the agent loop and surfaced through manifest sync. See [`ARCHITECTURE.md` §7](../ARCHITECTURE.md) for defaults.

## Related docs

| Doc | Contents |
|---|---|
| [`author-workflow.md`](./author-workflow.md) | Author state sequence and failure paths |
| [`repair-workflow.md`](./repair-workflow.md) | Repair state sequence and failure paths |
| [`contracts.md`](./contracts.md) | Schema boundaries and contract-first flow |
| [`validation.md`](./validation.md) | Four-tier validation architecture |
| [`sandbox-and-artifacts.md`](./sandbox-and-artifacts.md) | Filesystem safety and reserved paths |
| [`failure-modes.md`](./failure-modes.md) | Bounded loops, repair limits, error codes |
| [`AUTHOR_VALIDATION_MODEL.md`](./AUTHOR_VALIDATION_MODEL.md) | Author validation layer detail |
| [`FAULT_TOLERANCE_MODEL.md`](./FAULT_TOLERANCE_MODEL.md) | User-facing failure mitigation |
| [`TOOL_SCOPE_MODEL.md`](./TOOL_SCOPE_MODEL.md) | Phase-scoped tool exposure |
| [`SESSION_STATE_AND_RESUME.md`](./SESSION_STATE_AND_RESUME.md) | Resume and orphan recovery |
