# Failure modes and bounded loops

Typed `error_code` values and detection rules: [`ARCHITECTURE.md` §8](../ARCHITECTURE.md). User-facing mitigation: [`FAULT_TOLERANCE_MODEL.md`](./FAULT_TOLERANCE_MODEL.md).

## Session budgets (INV-12)

| Budget | Default | Terminal status |
|---|---|---|
| Tokens | 150,000 | `failed_budget` |
| Tool calls | 40 | `failed_budget` |
| Agent steps | 25 (author) / 20 (repair) | `failed_budget` |
| Wall time | 1,500 s | `failed_budget` |
| Generated files | 20 | rejection at write tool |

Warn at 75% for tokens, tool calls, steps, and wall time.

## Author execution repair

After agent generation, runtime/pytest/contract/safety failures trigger bounded model repair:

| Setting | Default | Location |
|---|---|---|
| `author_max_repair_attempts` | 1 | `config.py` |

Repair stages candidates under `generated/repairs/attempt_{N}/`. Pytest repair promotes only after candidate suite passes. Exhaustion emits `workflow_failed` with tier-specific detail.

Increase attempts for larger workflows via environment variable `AUTHOR_MAX_REPAIR_ATTEMPTS`.

## Repair patch iteration

| Guard | Limit |
|---|---|
| `repair_review_patch` → `repair_diagnose` cycles | 2 |
| Repair agent loop steps | 20 |

Enforced in `orchestrator/state_machine.py`, not via a separate config knob.

## Agent loop circuit breaker

Same tool + same arguments repeated triggers termination inside `AgentLoop` (prevents infinite ReAct). Independent of repair attempt counters.

## Schema repair bounds

Pydantic validation at API/tool boundaries: exactly one structured re-prompt before escalation (`validation_loop_exhausted` → `failed_model`).

Contract planning/review/codegen each have bounded JSON repair paths in `author_llm_authoring.py`.

## Orphan recovery

If the API restarts while a session row is `running` with no background task:

- `runner.recover_orphaned_running_session` reconciles to prior `workflow_failed` if present
- Otherwise records `workflow_interrupted` and sets `failed_other`

Startup calls `recover_all_orphaned_running_sessions`.

## Event log corruption

If generated code overwrites `events.jsonl` with non-`WorkspaceEvent` JSON:

- `_last_event_id` raises `EventLogError` on next append
- Prevents silent chain breakage

Mitigation: static scan blocks reserved-path writes before execution.

## Malformed uploads and files

| Code | Recovery |
|---|---|
| `malformed_csv` | Re-upload |
| `unsupported_file_type` | Re-upload |
| `file_too_large` | Re-upload smaller file |
| `ambiguous_schema` | Answer clarification (`paused_user`) |

## Tests

| File | Coverage |
|---|---|
| `test_author_custom_workflow_gate.py` | Repair attempt bounds |
| `test_config_defaults.py` | Default repair attempt count |
| `test_runner_orphan_recovery.py` | Orphan session recovery |
| `test_fault_tolerance_user_mitigation.py` | User-facing failure mapping |
| `test_architecture_invariants.py` | Event log error paths |
