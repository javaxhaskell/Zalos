# Repair workflow

Detailed step tables: [`WORKFLOWS.md`](../WORKFLOWS.md). Phase guards: [`ARCHITECTURE.md` §6.3–6.5](../ARCHITECTURE.md).

## Entry points

| Path | Role |
|---|---|
| `POST /sessions` with `workflow=repair` | Create session |
| `POST /sessions/{id}/fixtures/{name}` | Load bundled broken agent |
| `POST /sessions/{id}/run` | Start `RepairFlow` via `runner.py` |
| `orchestrator/repair_flow.py` | Two-phase INFO → FIX orchestration |

## State sequence

Repair runs two bounded agent loops separated by covering approval grants:

```
Phase 1 — repair.info
  repair_upload → repair_loaded → repair_problem
  → repair_triage → repair_confirm_summary
  → repair_reproduce (must emit REPRODUCTION_RESULT)
  → repair_diagnose → repair_propose → repair_review_patch

Phase 2 — repair.fix (covering APPROVAL_GRANTED for write tools)
  → repair_apply → repair_validate → repair_report → repair_finalise
  → COMPLETED
```

### INFO loop requirements

- `record_reproduction` is the only path that emits `REPRODUCTION_RESULT`.
- `state_machine.transition` refuses `REPAIR_DIAGNOSE` without that event.
- Patch proposal must pass `validate_repair_proposal` before FIX phase.

### FIX loop requirements

- Patch applied via `apply_patch` (sandbox `patch -u -p1`).
- Post-fix pytest must reach 0 failures for completion.
- `reports/repair_report.md` with six sections is mandatory.

## Patch iteration bounds

| Guard | Limit | Config / code |
|---|---|---|
| Patch review cycles | Max 2 (`repair_review_patch` → `repair_diagnose`) | `state_machine` |
| Agent loop steps | 20 | `LoopBudgets` in repair flow |
| Same-tool-same-args | Circuit breaker | Agent loop |

See [`failure-modes.md`](./failure-modes.md) for Author execution repair limits (separate from Repair workflow patch cycles).

## Failure paths

| Condition | Status | Notes |
|---|---|---|
| Fixture load failure | `failed_other` | Missing or corrupt ZIP |
| Cannot reproduce | `paused_user` | Honest cannot-reproduce path |
| Diagnosis without reproduction | blocked | State machine guard |
| Patch rejected / invalid | loop back or terminal | Depends on iteration count |
| Post-fix pytest failures | `failed_other` | Evidence in events |
| Budget exhausted | `failed_budget` | Same caps as Author |
| User decline at approval | `failed_user_reject` | If terminal-step decline |

## Evidence gates

Repair completion requires, in order:

1. Before-fix failure observed (pytest or sample run)
2. Validated patch proposal
3. After-fix pytest pass (target: 0 failed)
4. Six-section repair report on disk
5. `completion_via: repair_validated_patch` in manifest

The model is advisory; deterministic gates own the terminal decision.
