# Author workflow

Step-by-step tables with tool names and file paths are in [`WORKFLOWS.md`](../WORKFLOWS.md). Phase enums and guards are in [`ARCHITECTURE.md` §6](../ARCHITECTURE.md).

## Entry points

| Path | Role |
|---|---|
| `POST /sessions` | Create session row + workspace |
| `POST /sessions/{id}/files` | Upload CSV/XLSX to `uploads/` |
| `POST /sessions/{id}/run` | Start background orchestrator (`runner.py`) |
| `orchestrator/author_custom_build.py` | LLM-first custom workflow pipeline |
| `orchestrator/author_llm_authoring.py` | Contract, code, and test generation stages |

## State sequence (custom Author path)

The primary production path is `execute_custom_workflow_pipeline` in `author_custom_build.py`:

```
1. profile upload          → inspect_file / inspect_csv_schema
2. date clarification    → optional PAUSED_USER (author_date_clarification)
3. model authoring       → contract_planning → contract_review → code_generation → test_generation
4. contract persist      → generated/author_output_contract.json (backend-managed)
5. safety scan           → static scan before execution
6. execute agent         → sandbox subprocess
7. generated pytest      → sandbox subprocess
8. four-tier validation  → universal → contract → pytest → golden (optional)
9. provenance sync       → contract hash gate, model_authoring_summary
10. archive + terminal   → COMPLETED or typed failure
```

### Model authoring sub-stages

Each stage emits `model_called` events. Contract JSON is validated strictly before codegen begins. Invalid contract payloads trigger bounded schema repair (one structured re-prompt per stage). See [`contracts.md`](./contracts.md).

### Execution tail (`_execute_contract_build_tail`)

After authoring succeeds:

```
preflight syntax → run agent → run pytest → validate layers
     ↓ failure                    ↓ failure
 execution_repair (≤ author_max_repair_attempts)
     ↓ exhausted
 WORKFLOW_FAILED + terminal status
```

Repair kinds: `runtime`, `pytest`, `contract_validation`, `safety`. Pytest repair promotes candidates from `generated/repairs/attempt_N/` only after candidate pytest passes.

## Failure paths

| Condition | Status | Typical error_code |
|---|---|---|
| Unreadable upload | `failed_other` | `author_custom_build_failed` |
| Date format ambiguous | `paused_user` | — |
| No model client | `failed_other` | `author_model_required_for_authoring` |
| Contract clarification needed | `paused_user` | — |
| Authoring stage failure | `failed_other` | stage-specific (`author_contract_planning_failed`, etc.) |
| Safety scan failure | `failed_other` | `safety_validation_failed` |
| Repair attempts exhausted | `failed_other` | `author_validation_failed` / tier-specific |
| Budget cap | `failed_budget` | `budget_exhausted_*` |
| User cancel | `failed_other` | `user_abandoned` |

All failures append `workflow_failed` (or pause events) to `events.jsonl` before terminal row update.

## Invariants (Author-specific)

- **INV-1**: Model output is parsed and validated; backend writes persisted artifacts.
- **INV-8**: `AuthorOutputContract` uses Pydantic strict + `extra="forbid"`.
- **INV-10**: Uploaded CSV content is data in prompts, not instructions.
- **INV-12**: Agent loop step/token/wall caps; execution repair bounded by config.

See [`validation.md`](./validation.md) for the four validation tiers.
