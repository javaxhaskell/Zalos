# ADR-0003 — Backend and execution model

**Status:** accepted
**Date:** 2026-05-21

## Context

The backend hosts the FastAPI surface, the bounded agent loop, the typed tool registry, the sandbox runner, the persistence layer, and the validation engine. It needs to be production-credible without consuming the build budget on infrastructure.

## Decision

- **FastAPI** (Pydantic v2 strict + `extra="forbid"`) for the HTTP surface.
- **SQLAlchemy 2.0** for SQLite access (sync engine; sync sessions). Alembic for migrations.
- **subprocess.run** for code execution, with:
  - `cwd` pinned to `${workspace_path}`.
  - Hard timeout (60s for `run_python_script`, 120s for `run_pytest`).
  - Minimal env (only `PATH`, `PYTHONPATH`).
  - Captured stdout/stderr, truncated at 1 MiB per stream (overflow to `outputs/_logs/${step}.log`).
- **Docker per-session sandbox** is documented in `DEPLOYMENT.md` as the production-extension upgrade; not implemented in the prototype.

The agent loop sits in `apps/api/src/agentforge/agent/loop.py` and adapts the OpenHands CodeAct pattern (see ADR-0001).

## Options considered

| Option | Verdict |
|---|---|
| **FastAPI + subprocess** | Selected |
| FastAPI + Docker per-session | Documented as production extension (ADR-0010); 3–6h to set up correctly |
| Flask + subprocess | No typed routes; rejected |
| Pure Python CLI | No UI integration; rejected |
| FastAPI + hosted sandbox (E2B / Modal) | Vendor dependency; documented as production extension |

## Rationale

- The assignment lists "subprocess workspace" explicitly as an acceptable execution model.
- FastAPI is Pydantic-native, matching the typed-contracts thesis.
- Subprocess + `cwd` pin + timeout + minimal env is sufficient isolation for synthetic data on a single-user laptop, which is the demo target. The Docker upgrade path is mechanical (the runner interface is the same).
- SQLite is sufficient for single-user durable state at prototype scale; Postgres is documented as a production extension (ADR-0004).

## Consequences

- Backend boots with `uvicorn` in a single process.
- The sandbox runner is a small wrapper around `subprocess.run`; well under 100 LOC.
- Every write-tool invocation requires an `idempotency_key` argument; the executor enforces this before dispatching.
- The agent loop is bounded by token / step / wall / tool-call budgets, checked at the top of every iteration.
- Generated code is executed against the user's uploaded sample only; no network access from inside the subprocess (the subprocess does not have credentials; the user's `ANTHROPIC_API_KEY` is not passed in).

## Reversal condition

- Move to Docker per-session if sandbox escape becomes a compliance requirement and the production-extension documentation is insufficient (not expected for this prototype), OR as part of productionisation.
- Move to a hosted sandbox (E2B, Modal) when multi-tenant deployment requires it.
