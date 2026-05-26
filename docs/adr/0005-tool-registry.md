# ADR-0005 — Tool registry

**Status:** accepted
**Date:** 2026-05-21

## Context

The assignment requires "tool calls for inspecting files, editing or generating code, running tests, validating outputs, and summarising results." Tool calls are also the load-bearing security boundary: the LLM proposes tool calls, and the backend dispatches only registered tools after validation, authorization, and (where applicable) approval.

## Decision

A single Python registry (`apps/api/src/agentforge/tools/registry.py`) holds 20 typed `ToolDefinition` entries. Each definition declares:

- `name` (unique identifier).
- `description` (what the model sees).
- `input_schema_name` (Pydantic class in `agentforge.schemas` validating tool args).
- `output_schema_name` (Pydantic class validating the tool's return).
- `risk_level` ∈ `read | low_write | high_write`.
- `requires_approval: bool` (default `True` for any write tool; opt-out requires `adr_override` citation).
- `idempotent: bool`.
- `phases` (list of workflow-phase names where the tool is enabled).
- `authorize` callable name.
- `adr_override` (ADR ID if `requires_approval=False` on a write tool).

The registry exposes `list_for_phase(phase)` to produce the Anthropic tool-use shape filtered to the current phase. Models see only the tools available in their current phase. Calls to unregistered tools or wrong-phase tools are rejected by the executor; the rejection is returned as an observation to the model.

Idempotency keys are derived deterministically: `sha256(session_id + tool_name + step + canonical_json(args))`.

## Options considered

| Option | Verdict |
|---|---|
| **Typed `ToolDefinition` registry with risk levels and coarse phase scoping** | Selected |
| Function-decorator dispatch (`@tool` annotation) | Lacks risk-level metadata; no phase scoping; rejected |
| String dispatch table | No typed I/O; rejected |
| Fine-grained per-state phase scoping | Over-engineered; coarse two-phase (info ⇄ build/fix) suffices |

## Rationale

- The registry is the security boundary; phase scoping narrows the model's option space and reduces error rate.
- Risk levels drive approval routing without coupling tool definitions to the approval engine.
- Idempotency keys derived from canonical args allow the executor to dedupe retries and surface conflicting-args replays.
- The 20-tool set is the minimum complete surface for both workflows (see CONTRACTS.md §4).
- Approval-default is `True` for writes; opting out (e.g., `generate_validation_report`, `generate_repair_report` which only write inside the workspace from event-log source data) requires an explicit `adr_override` field citing this ADR or a future one.

## Consequences

- The model can only invoke tools listed in the registry. Unregistered names raise `ToolNotRegisteredError`, which the executor converts into an observation telling the model "tool X is not available in phase Y; available tools: [...]".
- Every write tool's input schema includes an `idempotency_key: str` field.
- The executor enforces approval before any high-risk dispatch.
- New tools require a Pydantic schema in `agentforge.schemas` and a registry entry; both go through `agentforge-architect` via ADR.

## Reversal condition

- Move to fine-grained per-state phase scoping if evals reveal the model consistently calling correct-tool-wrong-state combinations.
- Add a "computer use" tool family (browser + GUI) only if the take-home is extended to legacy-UI workflows.
