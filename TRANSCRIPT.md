# AgentForge — Build Narrative

> Systems-engineering account of how AgentForge was designed and hardened: load-bearing invariants, validation-driven completion gates, typed tool boundaries, and evidence-backed Author/Repair workflows. AI coding assistants (required by the assignment) accelerated implementation; structural decisions were fixed before feature work and enforced in code and tests.

---

## Overview

AgentForge is a finance-user-facing app for authoring and repairing Python finance agents from synthetic CSV/XLSX samples.

The product thesis, fixed early and never softened: **the LLM proposes typed plans and edits; the backend deterministically validates, sandboxes, and records.** That split — model authority over *what to build*, orchestrator authority over *whether it may run or complete* — drove every structural choice.

The assignment required an open-source coding-assistant foundation, file-enabled workspaces, typed tool calls, synthetic fixtures, validation, and a build narrative. AI-assisted coding tools handled boilerplate and iteration speed; the load-bearing architecture — twelve invariants, frozen contracts, four validation tiers, append-only event log — was specified, reviewed, and tested explicitly.

---

## Architecture planning approach

### Requirements decomposition

| Axis | Author | Repair |
|------|--------|--------|
| **Goal** | Build a new agent from description + sample | Fix a broken agent from folder/ZIP + problem report |
| **Phases** | INFO → BUILD (coarse) | INFO → FIX |
| **Model role** | Authors contract, code, tests | Advisory on diagnosis; orchestrator owns evidence pipeline |
| **Completion gate** | `ai_authored_workflow_build` | `repair_validated_patch` |

Both workflows share a bounded `AgentLoop` (~280 LOC, OpenHands-inspired patterns reimplemented; no vendored code) driven by deterministic orchestrators (`AuthorFlow`, `RepairFlow`).

### Four validation tiers (Author)

Backend validators enforce; the model authors the spec:

| Tier | What it checks | Authority |
|------|----------------|-----------|
| **Universal** | Required artifacts, row preservation, safety, archive readiness | Backend — every workflow |
| **Contract-specific** | Columns, enums, keys, exception consistency from model-authored contract | Backend enforces model spec |
| **Generated pytest** | Model-authored workflow-specific tests | Model writes; backend runs |
| **Golden-output** | Independent expected-output comparison when staged | Validation-only — never injected into codegen |

Implementation: [`apps/api/src/agentforge/validation/validation_architecture.py`](./apps/api/src/agentforge/validation/validation_architecture.py).

### Tool registry vs orchestrator audit

- **Tool registry** — typed definitions, input/output schemas, phase/risk/approval metadata, idempotency keys (INV-7). The executor checks registration before dispatch.
- **Orchestrator** — owns phase transitions, covering approval grants (ADR-0006), evidence gates, and terminal status. The model cannot self-approve or skip prerequisites (e.g., Repair requires `record_reproduction` before diagnose).
- **Event log** — every tool call, model call, approval, and validation run is recorded in `events.jsonl` with hash-linked chain integrity.

### Session persistence and fault tolerance

| Layer | Mechanism |
|-------|-----------|
| **Session row** | SQLite — status, budget counters, terminal error codes |
| **Event log** | Append-only `events.jsonl` per workspace |
| **Manifest** | `manifest.json` — completion metadata, artifact index |
| **Workspace** | `.workspaces/<session-id>/` — uploads, generated/, working/, outputs/, reports/ |
| **Fault tolerance** | Typed `ErrorCode`s, cannot-reproduce handling, validation failure cards, preserved partial artifacts on failure |

Subprocess sandbox (cwd-pin + timeout) is the prototype execution boundary. Docker-per-session swap is documented in [`DEPLOYMENT.md`](./DEPLOYMENT.md).

### Orchestration vs model authority

```
User intent + sample CSV
        │
        ▼
┌───────────────────┐     model stages: contract_planning,
│  Model (LLM)      │     contract_review, code_generation,
│  proposes         │     test_generation
└─────────┬─────────┘
          │ typed tool calls
          ▼
┌───────────────────┐     registry check, approval gate,
│  Orchestrator +   │     sandbox run, validation tiers,
│  Tool executor    │     evidence gate → terminal status
└─────────┬─────────┘
          │
          ▼
   events.jsonl + manifest + archive.zip
```

The model never completes a workflow directly. `finalise_session` requires a prior covering grant and a passed validation gate.

---

## Building strategy

### Foundation-first, not feature-first

Before application code, the repo established:

- **Twelve load-bearing invariants** ([`.claude/skills/agentforge-thesis-keeper/`](./.claude/skills/agentforge-thesis-keeper/)) — cited by number in every scope decision
- **Frozen contracts** — `ARCHITECTURE.md`, `CONTRACTS.md`, `WORKFLOWS.md`, ten ADRs
- **Six-skill build system** — thesis-keeper + architect (advisory); backend, frontend, fixtures/evals, docs (implementation-owning)

The skills system forced "which file owns this concern?" on every prompt. When scope was negotiated (e.g., wizard write capability, eval runner placement), the thesis-keeper kept INV-1 and INV-2 visible so the UI never accidentally received executor authority.

### Validation-driven completion

Neither Author nor Repair emits `workflow_completed` without passing an evidence gate:

- **Author** — model-authored contract plan + review + code + tests, execution evidence, four-tier validation report, provenance artifacts
- **Repair** — before-fix failure, validated patch, after-fix all-green, repair report + patch diff

UI status derives from artifacts and events, not optimistic flags. Validation-driven fixes (e.g., removing deterministic Author scaffold paths, Repair "complete" without reports) were driven by failing gates, not by narrative.

### Demo evidence gates

Completion claims require inspectable workspace artifacts:

- `events.jsonl` — append-only audit chain with `prev_event_id` linking
- `manifest.json` — terminal status + `completion` metadata
- `reports/validation_report.md` or `reports/repair_report.md`
- `archive.zip` — downloadable bundle excluding raw uploads

Final demo sessions were re-run until both primary flows reached **GREEN** with documented session IDs (see [Final demo state](#final-demo-state)).

### Investigation before implementation

Large build prompts were decomposed when estimated LOC exceeded ~1.5K:

- **BP5** → 5a (agent loop) / 5b (write tools + approval) / 5c (Author orchestrator + validation) / 5d (real model client)
- **BP10** → 10a (archive packaging) / 10b (eval runner) / 10c (Playwright — deferred)

Each sub-prompt had its own verification gate. Integration risks surfaced in tighter loops rather than in a single 2.5K-LOC merge.

### No fake resume or tool scope

- **Resume** restores persisted session state (SQLite row + `events.jsonl` + workspace tree); it does not replay partial model work
- **Retry** is a full rerun — no incremental "fix the last step" shortcut
- **Tool registry** is the security boundary (INV-2): unregistered tools cannot execute; phase and risk metadata gate write operations
- **Reference samples** inform prompts only — they do not bypass the LLM-first Author path or become final artifacts

---

## Input/output framing

### Contracts as model-authored specs

Author flow requires the model to produce:

- `generated/model_contract_plan.json` — output schema, business rules, deliverables
- `generated/model_contract_review.json` — self-review pass before codegen
- `generated/agent.py` — executable agent code
- `generated/tests/test_agent.py` — workflow-specific checks

Backend [`author_contract_validation.py`](./apps/api/src/agentforge/orchestrator/author_contract_validation.py) validates the contract shape; [`validation/layers.py`](./apps/api/src/agentforge/validation/layers.py) enforces it against actual outputs.

### Deterministic validators as enforcement

Validators are backend-owned and run regardless of model confidence:

- Schema and column checks
- Business-rule and row-level invariants
- Sandbox pytest execution
- Optional golden comparison when an expected file is staged separately

A model-generated validation report is not sufficient — `validate_output` computes `overall_passed` independently.

### Golden as validation-only

Golden outputs (e.g., `evals/golden/expense_exception_review/`) exist for **independent verification**, not for steering code generation. When staged, golden comparison is tier 4; when absent, it is skipped — not faked.

### Reference samples as honest inputs

Bundled samples (`templates/bank_categoriser/`, reference CSV endpoints, blind eval inputs) provide **prompt context and local walkthrough material**. They do not:

- Copy checked-in workflow code into `generated/agent.py`
- Bypass model stages
- Emit scaffold completion events

Removing deterministic Author scaffold completion paths was an explicit architectural decision after observing false-green sessions.

---

## Major phases and decisions

### BP1–BP4 — Foundation

Monorepo skeleton, Pydantic v2 schemas, SQLite + Alembic, FastAPI routers, Next.js scaffold, CI matrix, foundation attribution (OpenHands patterns studied; no code imported). Typed tool registry + subprocess sandbox + workspace allocator + append-only event log.

**Load-bearing fix (BP3):** Dropped `strict=True` from `StrictModel` at the API boundary; kept `extra="forbid"`. JSON string→enum coercion is required at FastAPI ingress; `extra="forbid"` is the actual contract-drift guard.

### BP5–BP8 — Author workflow

Decomposed agent loop, write tools, approval endpoints, Author orchestrator, six-layer validation (now grouped into four tiers for reporting), E2E test driving bank categoriser to golden output, real Anthropic/Ollama client integration, Author wizard with event polling and approval panel wiring.

**Architectural call:** Covering approval grants fire between Author phases (ADR-0006). The UI approval panel is wired for future gates; the orchestrator pre-records grants in the current happy path.

### BP6, BP9 — Repair workflow

Two-phase Repair with evidence gate: reproduction → diagnose → propose patch → apply → re-test → validate → report. Invoice aging demo moved to **`invoice_aging_v2`** (boundary bug at 31 days); v1 retained for date-format history. Repair covering grants fire up-front (INFO needs `run_pytest`).

Removed hardcoded patch tables; proposals derive from failing tests + source (model first, evidence inference fallback).

### BP10–BP11 — Packaging, evals, polish

- **Archive** — `archive.zip` bundles manifest, events, generated/, working/, outputs/, reports/; excludes uploads
- **Eval runner** — three scenarios (A-01 author, R-01 repair, ADV-01 injection); inline-async for sub-10s response
- **BP10c deferred** — Playwright dual-smoke deferred; HTTP pytest + evals + live demo cover correctness; UI rendering is verified on the live demo
- **Budget banner** — live event-derived counters during run; persisted totals at flow termination

### BP12 + audit rounds — LLM-first Author hardening

Post-BP12 work focused on honest Author completion:

- Removed non-LLM Author completion mechanisms
- Added model stage gates (contract planning, review, codegen, testgen)
- Provenance artifacts and tamper detection tests
- Expense Exception Review as primary Author demo (replacing bank categoriser for live presentation)
- UI polish: expense review queue (triage-only), output preview, validation summary cards

---

## Final demo state

Evidence summary: [`reports/final_demo_evidence_summary.md`](./reports/final_demo_evidence_summary.md).

| Flow | Workflow | Session ID | Verdict | Key result |
|------|----------|------------|---------|------------|
| **Author** | Expense Exception Review | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` | **GREEN** | 5/5 pytest; four-tier PASS including golden 7/7; 5/7 rows flagged (EXP-001,002,004,005,007) |
| **Repair** | `invoice_aging_v2` boundary bug | `b8317f51-384a-469a-92d3-90443853a4c5` | **GREEN** | 5/2 → 7/0 pytest; patch `<=31` → `<=30` |

**Local URLs (local):**

- Author: <http://localhost:3000/author/98942274-b4fb-4e0d-a3b8-0b8144bc01db>
- Repair: <http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5>

**Demo order:** Repair first (~10 s); Author pre-opened completed session (~3 min walkthrough). Walkthrough: [`docs/LOCAL_WALKTHROUGH.md`](./docs/LOCAL_WALKTHROUGH.md).

### Inspectable artifacts (both sessions)

- `events.jsonl` — model calls, tool calls, validation runs, terminal status
- `manifest.json` — `completion_via`, LLM provider metadata
- `reports/validation_report.md` or `reports/repair_report.md`
- `archive.zip` — via UI download or `GET /sessions/{id}/archive.zip`

---

## Honest limitations

1. **No universal correctness guarantee.** Validators enforce the model-authored contract and staged golden when present. They do not prove correctness for all possible inputs or all finance domains.
2. **Retry is a full rerun.** Resume restores persisted session state so you can inspect outputs; a new attempt re-executes the pipeline from the start.
3. **Expense review queue is local-only.** UI triage decisions persist in sessionStorage; they do **not** mutate `outputs/output.csv` or `outputs/exceptions.csv`.
4. **Reference samples are not scaffolds.** They provide honest prompt context; Author still requires model-authored contract, code, and tests.
5. **Repair advisory LLM is optional.** One advisory call may assist diagnosis; the deterministic evidence pipeline owns patch validation and completion.
6. **Eval vs live demo drift.** CI `A-01` uses bank categoriser; live Author demo uses expense exception review. CI `R-01` uses `invoice_aging_v1`; live Repair demo uses **v2**.
7. **Playwright not in CI.** Browser-layer tests deferred; HTTP-side pytest + evals + typecheck cover the implementation; UI verified on live demo.
8. **Subprocess sandbox.** Prototype execution boundary; production path documented in [`DEPLOYMENT.md`](./DEPLOYMENT.md).
9. **Synthetic data only.** No real bank, ERP, payment, or accounting integration.

See also [`docs/adr/0010-scope-exclusions.md`](./docs/adr/0010-scope-exclusions.md).

---

## Tooling and implementation approach

| Area | AI-assisted coding | Engineering judgment |
|------|-------------------|----------------------|
| Schema drafting | First-draft Pydantic models, test scaffolding | Enum naming, strict-mode boundary fix, assertion tightening |
| Boilerplate tests | Integration suites for sessions, archive, evals | Boundary cases (404, 409, traversal), race-condition fixes |
| Documentation | Internal handoff refreshes, markdown renderer draft | Engineering rationale, claim verification against code paths |
| Architecture | Symptom diagnosis (e.g., Pydantic strict collapse) | Root-cause trade-off articulation, decomposition decisions, BP10c deferral framing |

Representative event-stream excerpt (Author happy path):

```json
{"kind":"workflow_started","payload":{"workflow":"author"}}
{"kind":"model_called","payload":{"stop_reason":"tool_use","usage":{"total_tokens":4521}}}
{"kind":"tool_invoked","payload":{"tool_name":"seed_template"}}
{"kind":"validation_run","payload":{"overall_passed":true}}
{"kind":"workflow_completed","payload":{"workflow":"author","steps_taken":7}}
```

The audit log is the source of truth (INV-6). `GET /audit/export/{id}` returns `chain_check.valid` confirming event-chain integrity.

---

## Verification status

Last completed local verification:

- Backend pytest: **309 passed**, 1 deselected (live-only round-trip)
- Focused Author/eval/budget smoke: **67 passed**
- Focused Repair regressions: **8 passed**
- Frontend typecheck + build: passed
- Eval suite (`make eval`): 3/3 scenarios pass

Commands: `make ci`, `make eval`, `make test-api`. Local walkthrough: [`docs/LOCAL_WALKTHROUGH.md`](./docs/LOCAL_WALKTHROUGH.md).

---

## Related documentation

| Document | Purpose |
|----------|---------|
| [`README.md`](./README.md) | Setup, run, demo paths, requirements map |
| [`docs/architecture.md`](./docs/architecture.md) | Module boundary map, model routing |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Component diagram, invariants, foundation attribution |
| [`CONTRACTS.md`](./CONTRACTS.md) | Canonical schemas, tool registry, event log shape |
| [`docs/SUBMISSION_CHECKLIST.md`](./docs/SUBMISSION_CHECKLIST.md) | Requirement → evidence mapping |
| [`docs/TAKE_HOME_REQUIREMENTS_COVERAGE.md`](./docs/TAKE_HOME_REQUIREMENTS_COVERAGE.md) | Detailed requirements checklist |
| [`HANDOFF.md`](./HANDOFF.md) | Internal build log (not primary reader documentation) |
