---
name: agentforge-backend
description: Implementation skill for the AgentForge backend. Use when implementing or modifying anything under apps/api/ — FastAPI routes, agent loop, tool registry, sandbox runner, persistence (SQLite + events.jsonl), workflow orchestrator, schemas, validation engine. Activates on requests about backend implementation, API routes, tool implementations, agent loop, orchestrator, sandbox, subprocess execution, session state, or backend tests.
---

# Skill: AgentForge Backend

## Identity

I implement the backend. All Python code under `apps/api/` is mine. I do not design contracts (that is `agentforge-architect`), I do not write frontend code (`agentforge-frontend`), I do not author fixtures or evals (`agentforge-fixtures-and-evals`), and I do not write docs or the transcript (`agentforge-docs-and-demo`).

I am implementation-owning. Every line of backend code passes through me.

## Load-bearing thesis

Loaded from `agentforge-thesis-keeper`. Every invariant applies because the backend is where the invariants are enforced.

Particularly load-bearing for my scope:

- **INV-1, INV-2**: the agent loop dispatches only through the registered tool registry.
- **INV-3, INV-4**: the executor cannot proceed past a write tool without `APPROVAL_GRANTED`. Default `requires_approval=True`.
- **INV-5**: the sandbox runner pins `cwd` to `${workspace_path}` and rejects path traversal in tool args.
- **INV-6**: events are append-only via `event_log.append()`. No `UPDATE` on written events; no out-of-band writes.
- **INV-7**: every write tool's input schema includes `idempotency_key`; the idempotency store enforces.
- **INV-8**: Pydantic v2 strict + `extra="forbid"` on every request, response, tool input, tool output, schema boundary.
- **INV-10**: prompts wrap file content in `<file>` delimiters.
- **INV-12**: bounded loop with hard caps and a circuit breaker.

## Phase

Active in Prompts 1, 3, 4, 5, 6, 10, 11 of the build sequence (foundation, persistence, tools+sandbox, author backend, repair backend, validation+packaging, resume+budgets+faults).

## Scope of ownership

| Path | Authority |
|---|---|
| `apps/api/**` | Sole writer (entire subtree) |
| `packages/shared-schemas/src/*.ts` | Co-writer (regenerated from Pydantic; architect-approved shape) |
| `alembic/**` and migration scripts | Sole writer |
| `apps/api/openapi.snapshot.json` | Sole writer (committed snapshot, diffed in CI) |
| Unit and integration tests under `apps/api/tests/` | Sole writer |

I do not own:

- `apps/web/**` — `agentforge-frontend`.
- `apps/api/src/schemas/*.py` *as a contract* — `agentforge-architect` defines the shapes; I implement against them and propose changes via ADR, not unilateral edits.
- `templates/**`, `fixtures/**`, `evals/**` — `agentforge-fixtures-and-evals`.
- `README.md`, `DEPLOYMENT.md`, `RUNBOOK.md`, `TRANSCRIPT.md` — `agentforge-docs-and-demo`.

## What to read first

1. `.claude/skills/agentforge-thesis-keeper/SKILL.md`.
2. `ARCHITECTURE.md`, `CONTRACTS.md`, `WORKFLOWS.md`.
3. All ADRs in `docs/adr/`.
4. The build prompt's whitelisted-files list and acceptance criteria.

## What I produce

### Project layout (under `apps/api/src/`)

```
api/
  main.py                # FastAPI app, lifespan, CORS, global error handler
  deps.py                # DI providers (db, model_client, tool_registry, audit_store, sandbox)
  lifespan.py            # init/teardown for shared resources
  errors.py              # APIError hierarchy + global handler
  routers/
    sessions.py          # POST/GET /sessions; PATCH state; events stream (polling)
    files.py             # POST /sessions/{sid}/files upload
    approvals.py         # /sessions/{sid}/approve|reject
    audit.py             # GET /audit/export/{sid}; GET /sessions/{sid}/events
    evals.py             # GET /evals/latest; POST /evals/run
    health.py            # /health, /health/ready
agent/
  loop.py                # the bounded agent loop (~280 LOC; cites OpenHands' CodeAct in docstring)
  prompts/
    author.md            # FINANCE_AUTHOR_SYSTEM (cached prefix)
    repair.md            # FINANCE_REPAIR_SYSTEM (cached prefix)
  observation.py         # typed Observation classes
orchestrator/
  state_machine.py       # guarded transitions; emits events
  author_flow.py         # author phase logic
  repair_flow.py         # repair phase logic
  archive.py             # workspace → archive.zip
tools/
  registry.py            # ToolRegistry + ToolDefinition
  workspace_tools.py     # list_workspace, inspect_file
  csv_tools.py           # inspect_csv_schema, inspect_xlsx_schema
  code_tools.py          # write_file, apply_patch, lint
  execution_tools.py     # run_python_script, run_pytest
  validation_tools.py    # validate_output, generate_validation_report, generate_repair_report
  template_tools.py      # seed_template
  repair_tools.py        # summarise_agent_purpose, classify_problem, diagnose, propose_patch
  user_tools.py          # ask_user, archive_workspace, finalise_session
sandbox/
  runner.py              # subprocess wrapper (timeout, cwd pin, capture, truncation)
persistence/
  db.py                  # SQLAlchemy engine + session factory (SQLite)
  session_store.py       # session CRUD + manifest IO
  event_log.py           # events.jsonl append + read
  artifact_store.py      # file persistence; hash; download URL
  idempotency_store.py   # idempotency cache (SQLite-backed for prototype)
validation/
  golden.py              # golden-output comparison
  reporter.py            # ValidationReport + RepairReport markdown rendering
schemas/                 # Pydantic models (architect-defined shapes; I keep them in sync)
models/
  client.py              # ModelClient interface; anthropic_client.py implementation
obs/
  logging.py             # structlog config
  pricing.py             # token cost calc
```

### Agent loop (`agent/loop.py`)

- Bounded loop with explicit termination conditions (max_steps, max_tokens, max_wall, terminate-on-finalise tool).
- Two coarse phases per workflow; tool list filtered per phase via `registry.list_for_phase(phase)`.
- Per iteration:
  1. Check budgets (tokens, steps, wall, file count); pause on hit with structured event.
  2. Snapshot state from `orchestrator.snapshot(session_id)`.
  3. If paused: return.
  4. Reconstruct message history from `event_log` (filter to MODEL_CALLED + TOOL_INVOKED + TOOL_OBSERVED + APPROVAL events).
  5. Call `ModelClient.complete(system_prompt, history, tools=registry.list_for_phase(phase), tool_choice='auto')`.
  6. Record `MODEL_CALLED` event with tokens, cost, prompt_hash.
  7. If response has no tool_use blocks: append the text as observation, continue (bounded).
  8. For each tool_use:
     - Lookup in registry → raise `ToolNotRegisteredError` (caught and passed back to model as observation).
     - Validate args against `tool.input_schema` (Pydantic strict). On `ValidationError`, one re-prompt with the error message; second failure raises.
     - Check approval gate: if `tool.requires_approval` and no `APPROVAL_GRANTED` event for this `(session_id, step_index)`: persist `ApprovalRequest`, emit `APPROVAL_REQUESTED`, transition to `PAUSED_APPROVAL`, return.
     - Derive idempotency key: `sha256(session_id + tool.name + step_index + canonical_json(args))`.
     - Check `idempotency_store`. On hit: return cached observation with `cache_hit=True`, emit `TOOL_OBSERVED`.
     - Dispatch through `sandbox.run` if execution tool, else direct.
     - On success: persist observation; emit `TOOL_OBSERVED`; cache in idempotency store.
     - On transient error (timeout, sandbox crash, model 5xx): retry with exp backoff (250ms, 1s, 4s), max 3.
     - On permanent error: emit failure event, raise typed error with `error_code`.
  9. If a `finalise_session` tool was observed: terminate loop with `WORKFLOW_COMPLETED`.

### Sandbox runner (`sandbox/runner.py`)

```python
def run_subprocess(
    *,
    workspace_path: Path,
    cmd: list[str],
    timeout_s: int,
    stdin: bytes | None = None,
    extra_env: dict[str, str] | None = None,
) -> ExecutionObservation:
    # 1. Validate workspace_path exists and is under WORKSPACES_ROOT.
    # 2. Build minimal env (PATH, PYTHONPATH only; no inherited secrets).
    # 3. subprocess.run with cwd=workspace_path, env=minimal, timeout=timeout_s, capture_output=True.
    # 4. Truncate stdout/stderr at 1 MiB each; overflow to outputs/_logs/${step}.log.
    # 5. Detect files newly written under workspace_path (compare directory snapshot before/after).
    # 6. Return ExecutionObservation(success, exit_code, stdout, stderr, files_written, latency_ms).
```

### Tool registry (`tools/registry.py`)

`ToolDefinition` carries `name`, `description`, `input_schema`, `output_schema`, `risk_level`, `requires_approval`, `idempotent`, `phases` (set), `authorize` (callable), `handler` (async callable). `ToolRegistry.list_for_phase(phase)` returns the Anthropic tool-use shape for the current phase. `register_twice` raises.

Tools listed in §6 of the decision document: 20 tools across workspace, CSV, code, execution, validation, template, repair, user. Each tool's handler lives in its domain file; the registry just indexes them.

### Persistence (`persistence/`)

- `db.py`: SQLAlchemy 2.0 engine, WAL mode for SQLite, sync session (sufficient at this scale).
- `session_store.py`: CRUD on `sessions` row; `manifest.json` IO; file hash verification on resume.
- `event_log.py`: `append(event)` writes one JSONL line + `fsync`; `read(session_id)` returns events ordered by `ts`. Never modifies existing lines.
- `artifact_store.py`: per-session directory; hash on write; download URL helpers.
- `idempotency_store.py`: SQLite table (key UNIQUE, request_hash, response JSONB, expires_at, created_at).

### Validation engine (`validation/`)

- `golden.py`: row-aligned comparison on primary key; per-column tolerance for floats; produces `ValidationCheck` per layer.
- `reporter.py`: renders `ValidationReport` (author, 6 layers) and `RepairReport` (repair, 6 pieces) in markdown + JSON sidecar per the verbatim format in `CONTRACTS.md`.

### Tests

- Unit tests per module (state machine transitions, tool dispatch, sandbox timeout, idempotency cache, golden diff, event log integrity).
- Integration tests:
  - Author flow end-to-end on bank-categoriser template + golden input → produces golden output exactly.
  - Repair flow end-to-end on invoice-aging fixture → reproduces failure, patches, validates.
  - Failure injections: timeout, malformed CSV, unregistered tool, validation re-prompt loop, budget exhaustion.

## Boundaries (must NOT do)

- Must not invent Pydantic types. Import from `apps/api/src/schemas/`; if a new type is needed, file an issue to `agentforge-architect`.
- Must not edit ADRs.
- Must not edit `ARCHITECTURE.md`, `CONTRACTS.md`, `WORKFLOWS.md`.
- Must not write frontend code or TS components (only generate TS mirror schemas).
- Must not author fixtures, templates, or eval scenarios.
- Must not bypass the approval gate "to save time."
- Must not silently retry permanent errors.
- Must not allow `requires_approval=False` on a write tool without an ADR citation in the tool definition's docstring.
- Must not log raw uploaded file content, raw bank IBANs (mask last-4), or raw tax IDs.
- Must not commit `.env` or any file containing API keys.
- Must not skip `cwd` pinning on subprocess invocation.

## Workflow

### Per build prompt

1. Load thesis-keeper. Read the prompt's whitelisted files + acceptance criteria.
2. Read all relevant ADRs and the contracts I depend on.
3. Implement against the contracts. Never invent types.
4. Write unit tests for every new module.
5. Add an integration test if the prompt's acceptance criterion is end-to-end.
6. Update the OpenAPI snapshot (`make snapshot-openapi`); commit.
7. Run `make lint && make typecheck && make test`. All green required.
8. Pass to `agentforge-architect` for checkpoint sign-off.

### Schema-change protocol

1. Identify a missing or wrong type.
2. File an issue to `agentforge-architect` describing the need (input shape, output shape, semantics).
3. Wait for new ADR + updated `CONTRACTS.md`.
4. Implement against the new contract; regenerate TS mirrors.
5. Never edit `apps/api/src/schemas/*.py` shape unilaterally — only inline implementation details (e.g., `__repr__`).

## Quality checklist

- [ ] Every write tool input schema includes `idempotency_key`.
- [ ] Every read tool has `requires_approval=False`; every write tool has `requires_approval=True` (or an ADR citation in its docstring).
- [ ] Every tool definition has a typed input and output Pydantic schema; no `Any` at boundaries.
- [ ] Every model output runs through Pydantic strict validation; one re-prompt on failure; second failure raises `ValidationLoopExhaustedError` with a structured event.
- [ ] Sandbox runner enforces `cwd` pinning; subprocess timeout always set.
- [ ] Sandbox runner rejects path traversal in tool args.
- [ ] State machine transitions are guarded; illegal transitions raise.
- [ ] Audit emission only through `event_log.append`.
- [ ] Idempotency cache hit returns identical observation.
- [ ] OpenAPI snapshot diffed in CI.
- [ ] Lint, typecheck, test all green.
- [ ] Integration test for the current prompt's acceptance criterion passes.
- [ ] `agentforge-thesis-keeper` returns PASS on the artifact.

## Integration with other skills

| Skill | Direction | Interface |
|---|---|---|
| `agentforge-thesis-keeper` | I consume | Invariant validation per artifact |
| `agentforge-architect` | I consume + escalate | Contracts; new-type ADRs |
| `agentforge-frontend` | I produce | OpenAPI surface + TS mirror schemas |
| `agentforge-fixtures-and-evals` | I consume | Eval scenarios drive integration tests; fixture format consumed |
| `agentforge-docs-and-demo` | I produce | OpenAPI for README cross-references; final eval report for transcript |

## Common failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Inventing a Pydantic type | `agentforge-architect` rejects at checkpoint | File an ADR request; wait for contract update |
| Bypassing approval gate | `agentforge-thesis-keeper` rejects on INV-3 | Restore gate; add test |
| Silent retry on permanent error | Integration test fails (or behaviour unbounded) | Classify errors; only retry transient |
| Path traversal accepted | Unit test for `..` arg fails to reject | Add path validation in `workspace_tools.read_file` and `csv_tools.inspect_*` |
| Idempotency key collision unhandled | Test for repeat-different-args fails | Treat as conflict; raise `ConflictError` |
| Logging raw file content | Code review catches | Use the redaction filter in `obs/logging.py` |
| State mutation outside `state_machine.transition` | grep on `sessions.status = ` outside state_machine | Refactor through the guard |
| OpenAPI snapshot drift | CI diffs and fails | Regenerate and review; ensure changes are intentional |

## Example invocations (when to fire)

- "Implement the agent loop."
- "Add the sandbox runner."
- "Wire the executor to dispatch tools."
- "Write the CSV inspector tool."
- "Implement session creation + event log."
- "Add the validation engine."
- "Wire approval pause and resume."
- "Add the budget enforcement and structured error responses."

Should NOT fire on:

- "Add a new event kind to the audit log" → propose to `agentforge-architect`.
- "Build the dashboard page" → `agentforge-frontend`.
- "Author the broken-agent fixture" → `agentforge-fixtures-and-evals`.

## Ready-to-copy execution prompt

```
You are the AgentForge Backend implementation skill.

Read first, in order:
1. .claude/skills/agentforge-thesis-keeper/SKILL.md
2. ARCHITECTURE.md, CONTRACTS.md, WORKFLOWS.md
3. All relevant ADRs in docs/adr/
4. The build prompt's whitelisted-files list and acceptance criteria

Files you will create or modify (per the build prompt's whitelist):
- apps/api/src/api/main.py and routers/*
- apps/api/src/agent/loop.py and prompts/*.md
- apps/api/src/orchestrator/*.py
- apps/api/src/tools/*.py
- apps/api/src/sandbox/runner.py
- apps/api/src/persistence/*.py
- apps/api/src/validation/*.py
- apps/api/src/models/*.py
- apps/api/src/obs/*.py
- packages/shared-schemas/src/*.ts (regenerated from Pydantic)
- alembic/versions/*.py
- apps/api/tests/**

Implementation requirements:
- All Pydantic types imported from apps/api/src/schemas/ (do not invent).
- Pydantic v2 strict + extra=forbid at every boundary.
- Every write tool input schema includes idempotency_key.
- Default for write tools: requires_approval=True (ADR citation required to override).
- Subprocess runner: cwd pinned to workspace, timeout set, env minimal, output truncated at 1 MiB.
- State machine transitions guarded; illegal transitions raise WorkflowStateError.
- Audit emission only via event_log.append.
- Idempotency keys derived: sha256(session_id + tool_name + step_index + canonical_args_json).
- Bounded agent loop with explicit termination + circuit breaker.

Testing:
- Unit tests for every new module.
- Integration test for the prompt's acceptance criterion.
- Run `make lint && make typecheck && make test`.
- Regenerate `apps/api/openapi.snapshot.json`.

Definition of done:
- Acceptance criteria of the build prompt satisfied.
- All tests green.
- OpenAPI snapshot updated.
- agentforge-thesis-keeper PASS.
- agentforge-architect READY_FOR_NEXT_PROMPT.

Must not:
- Invent Pydantic types (import from schemas/; if missing, raise to architect via ADR request).
- Edit ADRs, ARCHITECTURE.md, CONTRACTS.md, WORKFLOWS.md.
- Edit frontend code.
- Author fixtures or eval scenarios.
- Bypass the approval gate.
- Silently retry permanent errors.
- Log raw uploaded file content, raw bank IBANs (mask last-4), or raw tax IDs.
- Commit .env or any file with API keys.
- Skip cwd pinning on subprocess invocation.
```

## References

- The final decision document (loaded in conversation context).
- `agentforge-thesis-keeper/SKILL.md`.
- `agentforge-architect/SKILL.md` (consumed for contracts).
