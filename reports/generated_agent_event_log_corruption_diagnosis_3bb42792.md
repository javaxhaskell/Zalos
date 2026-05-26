# Generated Agent Event Log Corruption Diagnosis: 3bb42792-0bbe-4a74-aeb0-b59d3ad46dfe

## Summary

Session `3bb42792-0bbe-4a74-aeb0-b59d3ad46dfe` (`expense_exception_review`, DeepSeek blind eval) reached deterministic validation and produced workflow outputs, but the pipeline crashed with `KeyError: 'id'` in `event_log._last_event_id` when the backend attempted to append a post-execution audit event.

The generated agent **overwrote** `events.jsonl` with ad-hoc JSON lines that lack the `WorkspaceEvent` schema (`id`, `session_id`, `kind`, etc.).

## Exact Crash

```
KeyError: 'id'
  at event_log._last_event_id → UUID(parsed["id"])
```

Triggered after generated agent execution succeeded and the orchestrator attempted `EventLog.append()` for finalisation/archive events.

## Evidence Table

### A. Generated Agent Evidence

| Question | Answer |
| --- | --- |
| Did generated code reference `events.jsonl`? | **Yes** — lines 292–296 in `generated/agent.py` |
| Write mode | **Overwrite** — `open(events_path, 'w')` truncates the backend audit log |
| Other reserved writes | `manifest.json`, `SESSION_README.md`, `archive.zip`, `reports/model_authoring_summary.md` |
| Safety scan before execution | **Passed** — scanner only checked eval/exec/network; not reserved paths |
| Pytest | Advanced past prior pathing blocker; agent invoked during tests and also wrote reserved files |
| Contract-declared data outputs | Correct — `outputs/output.csv`, `outputs/exceptions.csv`, `reports/validation_report.md` |

### B. Corruption Evidence

| Question | Answer |
| --- | --- |
| `events.jsonl` line count after agent run | 2 lines (entire prior audit chain replaced) |
| Malformed line sample | `{"event": "processing_started", "timestamp": "2026-05-25 12:52:34.392916"}` |
| Has `id` field? | **No** |
| Has `kind` / `session_id` / `prev_event_id`? | **No** |
| Overwrite vs append | **Overwrite** — agent opened with mode `'w'` |

### C. Backend Evidence

| Question | Answer |
| --- | --- |
| Who owns `events.jsonl`? | Backend `EventLog.append()` only (INV-6) |
| `_last_event_id` behaviour | Reads last non-empty line, `json.loads`, expects `parsed["id"]` |
| Malformed-line handling on read | `read_all()` raises `EventLogError` with parse context |
| Malformed-line handling on append | **None** — `_last_event_id` raises raw `KeyError` |
| Sandbox CLI paths passed to agent | `--input`, `--contract`, `--row-output`, `--report-path` only |
| Backend finalisation expects | Valid prior audit chain in `events.jsonl` |

### D. Root-Cause Classification

**Primary: generated agent wrote backend-managed orchestration artifacts (prevention gap)**

1. Blind-eval user prompt lists `events.jsonl`, `manifest.json`, `SESSION_README.md`, `archive.zip` as expected outputs.
2. Contract planning copied these into `requested_deliverables` with `required=true` and `source=user_explicit`.
3. Codegen produced agent logic to create all listed deliverables, including overwriting `events.jsonl`.
4. Static safety scan did not forbid reserved-path writes, so execution proceeded.

**Secondary: backend append path surfaced corruption as opaque `KeyError`**

`_last_event_id` assumes the last line is a valid `WorkspaceEvent` JSON object with an `id` key.

## Generated Code Snippet (reserved writes)

```python
# Write events.jsonl (placeholder)
events_path = 'events.jsonl'
with open(events_path, 'w') as f:
    f.write('{"event": "processing_started", "timestamp": "' + str(datetime.utcnow()) + '"}\n')
    f.write('{"event": "processing_completed", ...}\n')
```

## Proposed Fix

1. **Safety scan (primary):** Detect writes to backend-managed paths (`events.jsonl`, `manifest.json`, `SESSION_README.md`, `archive.zip`, `generated/author_output_contract.json`, `reports/model_authoring_summary.md`, `generated/model_responses/`, `generated/debug/`) via AST + regex; block execution and trigger safety repair.
2. **Codegen prompt:** Explicitly forbid generated agents from writing orchestration/audit/control files; limit writes to CLI args and contract-declared workflow outputs.
3. **Safety repair prompt:** Same reserved-path prohibition with recheck expectation.
4. **Contract guidance/validation:** Mark backend-managed paths as not agent-produced even when mentioned in user prompts.
5. **Event log (secondary):** Raise `EventLogError` with corruption context instead of raw `KeyError`.

## Risks

- Over-broad path matching could false-positive on benign string literals in comments/docstrings — mitigated by write-context AST analysis.
- Contract planning may still mention backend artifacts in informational deliverables — validation downgrades/rejects `required=true` for backend-managed paths.
- Safety repair adds one model round-trip when codegen introduces reserved writes.

## Invariants Preserved

- Generated agents are **not** taught AuditEvent format.
- Generated code is **not** allowed to write `events.jsonl`.
- Event log validation is **not** weakened.
- Malformed records are **not** silently ignored.
- No fake successful archives or bypass of codegen/tests/validation.
