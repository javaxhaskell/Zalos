# AgentForge

Finance teams routinely need small Python agents that turn CSV/XLSX samples into repeatable workflows—and need a safe way to fix those agents when outputs drift. AgentForge is a prototype workspace for **Author** (build agents from sample data + plain-English intent) and **Repair** (diagnose and patch existing agents from a problem report). The model proposes typed plans and edits; the backend validates, sandboxes, executes, and records every step. **Synthetic data only.**

---

## Problem and approach

| Pain | AgentForge response |
|------|---------------------|
| Ad-hoc scripts with no audit trail | Append-only `events.jsonl` with hash-chained integrity; downloadable archives |
| Model output applied without checks | Four-tier Author validation; Repair evidence gates before completion |
| Unsafe execution on host | Typed tool registry (INV-2); subprocess sandbox with cwd-pin and timeouts |
| Silent “success” on bad runs | Fail-closed orchestration—missing reports, failed pytest, or budget exhaustion block completion |

**Thesis (load-bearing):**

> The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records. The model never touches the user's workspace without recorded approval. Tools are a typed registry; sandboxes are bounded; audit is append-only. Synthetic data only.

Twelve invariants follow from this thesis (enumerated in [`.claude/skills/agentforge-thesis-keeper/SKILL.md`](./.claude/skills/agentforge-thesis-keeper/SKILL.md)). Examples: **INV-2** — only registered tools execute; **INV-3** — write tools require recorded `APPROVAL_GRANTED`; **INV-12** — bounded loops (25 author / 20 repair steps, 150k tokens, 1500s wall).

AgentForge is **not** a production finance platform: no real bank/ERP integration, no multi-tenant auth, subprocess sandbox (Docker per session documented as a production extension in [`DEPLOYMENT.md`](./DEPLOYMENT.md)).

---

## Architecture

```
Next.js wizard (Author / Repair wizards, audit, eval admin)
        │ REST + polling
        ▼
FastAPI ──► Orchestrator (author_flow, repair_flow, state machine)
        │         │
        │         ▼
        │    Agent loop (~280 LOC, bounded, phase-scoped tools)
        │         │
        ├─────────┼──► Tool registry (20 typed tools)
        │         └──► Sandbox (subprocess, cwd-pin, timeout)
        ▼
Persistence: SQLite session index + per-session events.jsonl + workspace artifacts
```

### Author workflow (LLM-first, validation-gated)

Every completed Author run requires material model contribution (`model_calls > 0`) across contract planning, contract review, code generation, and test generation. Templates and reference samples are **prompt context only**—never copied as final outputs.

1. Profile uploaded CSV/XLSX (schema, keys, sample rows).
2. Model authors `AuthorOutputContract` → persisted under `generated/`.
3. Model reviews contract in a separate call.
4. Model authors `generated/agent.py` and generated tests.
5. Backend executes in sandbox, runs contract-driven validation (four tiers), optional independent golden comparison, provenance sync, archive.

Build label at completion: **AI-authored workflow build**. The legacy bank reference scaffold is off by default (`AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD=false`).

### Repair workflow (evidence-gated)

Load agent + problem report → reproduce failure (pytest) → diagnose → propose unified-diff patch → apply after approval → re-test → six-section `repair_report.md`. Completion requires `repair_validated_patch` evidence (e.g. bundled **`invoice_aging_v2`** boundary fixture: 5/2 → 7/0 pytest).

### Validation, sandbox, and audit

| Layer | Role |
|-------|------|
| **Four-tier Author validation** | Universal → contract-specific → generated pytest → golden output (independent, never in codegen prompts) |
| **Tool scope** | Phase-gated exposure (`author.info` → `author.build`, `repair.info` → `repair.fix`); see [`docs/TOOL_SCOPE_MODEL.md`](./docs/TOOL_SCOPE_MODEL.md) |
| **Sandbox** | Subprocess with cwd-pin, timeout, output cap; path traversal rejected |
| **Event log** | Append-only `events.jsonl`; manifest snapshots terminal metadata; audit export with chain verification |

Module boundaries, model routing, and workflow sequences: [`docs/architecture.md`](./docs/architecture.md), [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`WORKFLOWS.md`](./WORKFLOWS.md).

---

## Quick start

| Step | Command / URL |
|------|---------------|
| **Prerequisites** | Python 3.11+ with [`uv`](https://docs.astral.sh/uv/), Node 20+ with `pnpm` (or `npm`). DeepSeek API key **optional** for browsing pre-opened sessions; **required** for live Author (~3–4 min) or Repair (~10 s). Ollama or Anthropic also supported — see [Setup](#setup). |
| **Setup** | `cp .env.example .env` → `make setup` → `make migrate` |
| **Run** | `make up-daemon` (background) or `make up` / separate `make api` + `make web` — API **http://localhost:8000**, web **http://localhost:3000** |
| **Health** | `make dev-health` or `curl http://localhost:8000/health/ready` |
| **Stop** | `make down` |
| **Tests** | `make test-api` · `make test-web` · `make ci` (full matrix) |

```bash
git clone git@github.com:javaxhaskell/Zalos.git
cd Zalos
cp .env.example .env    # set DEEPSEEK_API_KEY for live runs
make setup && make migrate && make up-daemon
make dev-health
```

Setup takes roughly 2–5 minutes on a clean machine. `make setup` creates `.env` from `.env.example` when missing and initializes `.workspaces/`.

---

## Demo workflows

**Order:** Repair live first (~10 s), then Author (pre-opened session if workspace artifacts are present).

### Repair — Invoice Aging Boundary (`invoice_aging_v2`)

Dashboard → **Repair an existing agent** → **Invoice Aging Boundary Repair** → **Load fixture** → problem: *Invoices exactly 31 days overdue land in the wrong aging bucket.* → **Start agent**. Expect before 5 pass / 2 fail → patch `<=31` → `<=30` → after 7 pass / 0 fail.

Pre-opened session: [http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5](http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5)

### Author — Expense Exception Review

Dashboard → **Author a new agent** → **Expense Exception Review** → upload `blind_eval_cases/expense_exception_review/input.csv` (7 rows) → prompt:

> Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.

Pre-opened session: [http://localhost:3000/author/2bd70711-6fbd-422a-bf70-437b9f27f9e5](http://localhost:3000/author/2bd70711-6fbd-422a-bf70-437b9f27f9e5) (~275 s, 4/4 generated pytest, golden 7/7 PASS, four-tier validation PASS, contract hash gate PASS, 5/7 flagged).

Pre-opened URLs require matching workspaces under `.workspaces/<session-id>/` (not shipped in git). On a fresh clone without those artifacts, run Repair live or Author live with a configured model.

Step-by-step walkthrough: [`docs/LOCAL_WALKTHROUGH.md`](./docs/LOCAL_WALKTHROUGH.md).

---

## Key documentation

| Document | Purpose |
|----------|---------|
| [`docs/LOCAL_WALKTHROUGH.md`](./docs/LOCAL_WALKTHROUGH.md) | Step-by-step Author, Repair, resume, artifact inspection |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Component diagram, invariants, workspace layout, budgets |
| [`docs/architecture.md`](./docs/architecture.md) | Module boundaries, model routing, doc index |
| [`RUNBOOK.md`](./RUNBOOK.md) | Failure modes, event log inspection, budget recovery |
| [`TRANSCRIPT.md`](./TRANSCRIPT.md) | Build narrative — architectural decisions, demo evidence, limitations |
| [`CONTRACTS.md`](./CONTRACTS.md) | Schemas, tool registry, phase names, UX language map |
| [`docs/validation.md`](./docs/validation.md) · [`docs/AUTHOR_VALIDATION_MODEL.md`](./docs/AUTHOR_VALIDATION_MODEL.md) | Four-tier Author validation |
| [`docs/author-workflow.md`](./docs/author-workflow.md) · [`docs/repair-workflow.md`](./docs/repair-workflow.md) | State sequences and evidence gates |
| [`docs/sandbox-and-artifacts.md`](./docs/sandbox-and-artifacts.md) | Sandbox invariants, event log integrity |
| [`docs/FAULT_TOLERANCE_MODEL.md`](./docs/FAULT_TOLERANCE_MODEL.md) · [`docs/SESSION_STATE_AND_RESUME.md`](./docs/SESSION_STATE_AND_RESUME.md) | Fail-closed behaviour, session resume |
| [`docs/SUBMISSION_CHECKLIST.md`](./docs/SUBMISSION_CHECKLIST.md) | Capability → evidence map |
| [`DEPLOYMENT.md`](./DEPLOYMENT.md) | Cloud Run / Docker path, Postgres swap-in |

---

## Setup

### DeepSeek (default)

```bash
# .env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
AUTHOR_PLANNING_MODEL=deepseek-v4-pro
AUTHOR_REVIEW_MODEL=deepseek-v4-pro
AUTHOR_CODEGEN_MODEL=deepseek-v4-flash
AUTHOR_TESTGEN_MODEL=deepseek-v4-flash
AUTHOR_REPAIR_MODEL=deepseek-v4-flash
```

Stage-specific model names are configurable without code changes. After `make api`:

```bash
curl http://localhost:8000/health/ready
```

### Ollama (optional)

```bash
ollama serve
ollama pull qwen2.5-coder:14b
ollama pull qwen2.5-coder:7b
```

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_PLANNING_MODEL=qwen2.5-coder:14b
OLLAMA_CODEGEN_MODEL=qwen2.5-coder:7b
```

Planning/review uses the 14B model; codegen/testgen defaults to 7B for laptop latency. See [`docs/LOCAL_WALKTHROUGH.md`](./docs/LOCAL_WALKTHROUGH.md) §2.

### Anthropic (optional)

Set `LLM_PROVIDER=anthropic` and a valid API key from [console.anthropic.com](https://console.anthropic.com/).

---

## Run locally

Foreground (two terminals):

```bash
make api    # FastAPI at http://localhost:8000
make web    # Next.js at http://localhost:3000
```

Or single terminal: `make up` (requires `concurrently` via pnpm).

Background daemon (survives terminal exit):

```bash
make up-daemon
make dev-status
make dev-health
make dev-logs      # tail /tmp/agentforge-dev.log
make down
```

Eval admin surface: [http://localhost:3000/admin/evals](http://localhost:3000/admin/evals) — bundled scenarios for local regression evidence.

---

## Testing and CI

```bash
make test-api       # backend pytest
make test-web       # frontend typecheck + lint + build
make test           # both
make eval           # three bundled scenarios (A-01, R-01, ADV-01)
make ci             # lint, typecheck, tests, OpenAPI diff, evals
```

Focused commands:

```bash
cd apps/api && uv run pytest -q
cd apps/web && pnpm typecheck
make snapshot-openapi   # regenerate OpenAPI snapshot after API changes
```

**CI vs live demo paths (intentional drift):**

| Scenario | CI path | Live demo |
|----------|---------|-----------|
| Author | `A-01` bank categoriser | Expense Exception Review |
| Repair | `R-01` `invoice_aging_v1` | `invoice_aging_v2` boundary fixture |
| Adversarial | `ADV-01` CSV injection (INV-10) | — |

Fake-model tests prove orchestration only; real Author capability depends on the configured model passing evidence gates.

---

## Tool scope and fault tolerance

Tools are the only execution path. Each tool declares risk level, approval requirement, allowed phases, and idempotency in [`CONTRACTS.md`](./CONTRACTS.md) §4. High-risk execution (`run_python_script`, `run_pytest`) requires approval grants before running.

The system **fails closed**: model stalls, missing outputs, unsafe patch proposals, post-fix pytest failures, budget exhaustion, and declined approvals preserve artifacts and surface typed failures rather than marking workflows complete. Operational detail: [`RUNBOOK.md`](./RUNBOOK.md), [`docs/FAULT_TOLERANCE_MODEL.md`](./docs/FAULT_TOLERANCE_MODEL.md).

---

## Resume behaviour

Each workflow runs in a persisted session (SQLite row + `events.jsonl` + workspace `manifest.json`). Returning to `/author/{session-id}` or `/repair/{session-id}` reconstructs state from stored events and artifacts. Completed sessions show downloads; incomplete sessions show progress and the next action. **Retry = full rerun**; resume restores persisted state only. See [`docs/SESSION_STATE_AND_RESUME.md`](./docs/SESSION_STATE_AND_RESUME.md) and [`docs/LOCAL_WALKTHROUGH.md`](./docs/LOCAL_WALKTHROUGH.md) §7.

---

## Open-source foundation

AgentForge's agent loop is implemented in `apps/api/src/agentforge/agent/loop.py` (~280 lines). The action/observation model, event-stream-driven state, and bounded loop with explicit termination are adapted from OpenHands' CodeAct agent design (<https://github.com/All-Hands-AI/OpenHands>, commit `3515cb085834623d9d0ef9d6977d1bcfa2b6eb57`, principally the files `openhands/controller/agent_controller.py` and `openhands/events/`). No OpenHands code was imported, vendored, or copied; the patterns were studied and reimplemented in a minimal form tailored to AgentForge's two finance workflows. Everything else — the wizard UI, workspace, sandbox wrapper, finance-domain tool registry, workflow orchestrator, validation system, fixtures, evals, and repair report — is original. A fallback option (Aider-as-library) was considered and held in reserve; it was not used.

See [`docs/adr/0001-open-source-foundation.md`](./docs/adr/0001-open-source-foundation.md).

---

## Project layout

```
.
├── apps/
│   ├── api/src/agentforge/   # FastAPI, agent loop, orchestrator, tools, sandbox, validation
│   └── web/                  # Next.js wizard — /, /author/[sid], /repair/[sid], /admin/evals
├── packages/shared-schemas/  # OpenAPI-generated TypeScript types
├── templates/                # Synthetic reference inputs (bank categoriser)
├── fixtures/broken_agents/   # invoice_aging_v1 (CI), invoice_aging_v2 (live Repair demo)
├── blind_eval_cases/         # Canonical expense Author input (7 rows)
├── evals/                    # Scenarios, golden outputs, rubric
├── docs/adr/                 # Architecture decision records
├── ARCHITECTURE.md, CONTRACTS.md, WORKFLOWS.md, RUNBOOK.md, TRANSCRIPT.md
└── Makefile
```

---

## Known limitations

- **Sandbox:** subprocess + cwd-pin + timeout; Docker per session in [`DEPLOYMENT.md`](./DEPLOYMENT.md).
- **Auth:** demo cookie identity; SSO/RBAC are production extensions.
- **Persistence:** SQLite + per-session jsonl; Postgres append-only enforcement is a production extension.
- **Pre-opened sessions:** require local `.workspaces/{session-id}/` artifacts.
- **Expense review queue:** UI triage only (`sessionStorage`); does not mutate output CSVs.
- **Eval coverage:** bundled CI scenarios differ from live demo paths (see table above).
- **No Playwright suite:** HTTP tests + frontend typecheck/build cover current scope; rationale in [`TRANSCRIPT.md`](./TRANSCRIPT.md).
- **No real bank, ERP, payment, or accounting integration.**

Full exclusion list: [`docs/adr/0010-scope-exclusions.md`](./docs/adr/0010-scope-exclusions.md).

---

## Roadmap (production extensions)

1. **Hosted sandbox** (E2B or Modal) — swap `SandboxRunner` adapter only.
2. **Postgres + storage-level append-only audit** — `CHECK` + `REVOKE UPDATE` on events.
3. **Reference workflow library** — reusable synthetic inputs and prompts (context only, not final artifacts).
4. **Test-mode model injection endpoint** — gated route registration for UI automation without production exposure.

---

## License

MIT. See [`LICENSE`](./LICENSE).
