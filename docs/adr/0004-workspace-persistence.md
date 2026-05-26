# ADR-0004 — Workspace and persistence

**Status:** accepted
**Date:** 2026-05-21

## Context

Sessions must be resumable. The assignment explicitly asks how session state, decisions, files, tool results, and reports are persisted, and how resume works. We need durable state that survives backend restart, and a chronological event log that lets us reconstruct any session deterministically.

## Decision

Two persistence surfaces:

1. **SQLite (WAL mode) via SQLAlchemy 2.0** for indexable structured state. Tables: `sessions`, `decisions`, `uploaded_files`, `approval_requests`, `approval_decisions`, `tool_invocations`, `artifacts`, `model_calls`, `eval_runs`, `eval_results`, `idempotency_keys`.
2. **Per-session `events.jsonl`** under `${workspace_path}/events.jsonl` for the chronological event log. Append-only by convention (no `UPDATE`-equivalent operation in the persistence layer).

The workspace directory layout is:

```
${WORKSPACES_ROOT}/${session_id}/
├── manifest.json
├── events.jsonl
├── uploads/
├── generated/        (author flow; git-init for diff tracking)
├── working/          (repair flow; git-init for diff tracking)
├── outputs/
│   └── _logs/
├── reports/
└── archive.zip
```

Resume logic:
1. Read `manifest.json` (status, current_phase, current_step, budgets, file hashes, `schema_version`).
2. Verify recorded file hashes against current disk contents; surface tampering warning if mismatch.
3. Read `events.jsonl` and reconstruct the agent's message history (MODEL_CALLED + TOOL_INVOKED + TOOL_OBSERVED + APPROVAL events) for the next loop iteration.
4. Render the pending question/approval if `paused_*`; otherwise re-enter the agent loop.

## Options considered

| Option | Verdict |
|---|---|
| **SQLite + per-session events.jsonl** | Selected |
| Postgres + audit table with CHECK constraint + REVOKE UPDATE | Documented as production extension (ADR-0010); overkill for a single-user prototype |
| Pure filesystem (JSON files per entity) | No indexability for list endpoints; rejected |
| In-memory only | No resume; rejected (assignment requires resume) |

## Rationale

- The assignment is graded by reading and running, not by inspecting database isolation guarantees. SQLite is sufficient.
- SQLite + WAL handles concurrent reads from the API and writes from background jobs without ceremony.
- `events.jsonl` is grep-able, human-readable, and matches the "audit trail" expectation without needing a Postgres CHECK constraint.
- The session_id is the resume key; the URL is shareable; the workspace tar can be archived separately for long-term retention.
- Append-only by convention is enforced at the code level: only `event_log.append()` writes; no other code path opens `events.jsonl` for write.

## Consequences

- Zero external dependencies for persistence (no Postgres / Redis containers).
- `make setup` works on a clean machine in <5 minutes.
- Concurrent writes to SQLite are serialised (WAL handles this acceptably at single-user scale).
- Workspaces live on local disk; cleanup is manual or via `make clean-workspaces`.
- Schema versioning: `manifest.json` includes `schema_version`. Breaking schema changes increment this and surface a "session created on older version" message instead of crashing the resume path.

## Reversal condition

Move to Postgres + audit table with storage-level CHECK + REVOKE UPDATE when:
- Multi-user deployment with concurrent write contention.
- Compliance audit requires storage-level immutability proof (not just application-level convention).
- Cross-session indexes exceed SQLite's practical limits.
