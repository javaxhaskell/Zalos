# AgentForge — Architecture

> Status: final submission snapshot. Owned by `agentforge-architect`. Structural changes still require ADRs under `docs/adr/`.

## 1. Project thesis

The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records. The model never touches the user's workspace without recorded approval. Tools are a typed registry; sandboxes are bounded; audit is append-only. Synthetic data only.

## 2. Component diagram

```
┌──────────────────────────────────────────────────────────────────┐
│ Next.js 14 wizard (apps/web)                                     │
│ /  /author/[sid]  /repair/[sid]  /sessions/[sid]/audit  /admin   │
└────────────────────────────┬─────────────────────────────────────┘
                             │ REST + 2-second polling on /events
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI backend (apps/api/src/api)                               │
│ routers/{sessions,files,approvals,audit,evals,health}            │
│ deps · lifespan · errors (typed APIError hierarchy)              │
└──┬───────────────────────────────────────────────────────────┬───┘
   │                                                            │
   ▼                                                            ▼
┌─────────────────────────┐                ┌──────────────────────┐
│ Orchestrator            │                │ Persistence          │
│ state_machine           │                │ db (SQLite WAL)      │
│ author_flow             │                │ session_store        │
│ repair_flow             │                │ event_log (jsonl)    │
│ archive                 │                │ artifact_store       │
└──┬──────────────────────┘                │ idempotency_store    │
   │                                       └──────────────────────┘
   ▼                                                    ▲
┌─────────────────────────┐                              │
│ Agent loop              │  emits events / appends      │
│ ~280 LOC, bounded       │  via event_log.append        │
│ Two-phase tool exposure │──────────────────────────────┘
│ (info ⇄ build/fix)      │
└──┬──────────────────────┘
   │
   ▼
┌─────────────────────────┐                ┌──────────────────────┐
│ Tool registry           │                │ Sandbox runner       │
│ ToolDefinition[]        │──── dispatches│ subprocess.run with  │
│ 20 typed tools          │   exec tools  │ cwd pin, timeout,    │
│ phase-scoped exposure   │ ─────────────► │ 1 MiB output cap     │
└─────────────────────────┘                └──────────────────────┘
   │
   ▼
┌─────────────────────────┐                ┌──────────────────────┐
│ ModelClient             │                │ Validation engine    │
│ Anthropic Claude        │                │ golden + 6 layers    │
│ Sonnet 4.6 / Haiku 4.5  │                │ ValidationReport     │
│ prompt caching          │                │ RepairReport         │
└─────────────────────────┘                └──────────────────────┘
```

## 3. Component justifications

| Component | What | Why | What it replaces | Failure mode | Production-grade marker |
|---|---|---|---|---|---|
| Next.js wizard | Two-workflow finance-user surface | Finance users need structure (forms, panels, diffs); chat is dev-UX | Streamlit (looks like research), pure CLI (terminal-phobic users), chat-first | UI bug breaks demo; mitigated by Playwright smoke for both happy paths | 5 polished screens, shadcn/ui, typed routes, no `any` |
| FastAPI backend | Typed HTTP surface | Pydantic-native; OpenAPI for free; lifespan-managed singletons | Flask (no typed routes); pure CLI (no UI integration) | Boundary type drift; mitigated by OpenAPI snapshot diffed in CI | Pydantic v2 strict + extra=forbid; structured error envelopes |
| Orchestrator | Drives session state machine | Bounded, resumable, auditable workflow execution | Free-form ReAct loop | State corruption from un-guarded transitions; mitigated by `state_machine.transition` | Guarded transitions; illegal raises; every transition emits an event |
| Agent loop | Bounded model dispatch | OpenHands-inspired pattern: action/observation, event-driven, explicit termination | Unbounded ReAct | Stuck loop; mitigated by step cap + same-tool-same-args circuit breaker | Hard caps on tokens, steps, wall; one re-prompt on validation failure |
| Tool registry | Typed execution path | Security boundary: only registered tools can run | Function-decorator dispatch; string lookup | Drift; mitigated by phase-tag unit test | `ToolDefinition` with risk_level, idempotent, requires_approval, authorize |
| Sandbox runner | Bounded code execution | Subprocess with cwd pin + timeout; production swap is Docker | Direct `os.system`, eval() | Path traversal; mitigated by cwd resolution + rejection of `..` paths | Timeout always set; minimal env; output capped 1 MiB |
| Persistence (SQLite + events.jsonl) | All durable state | SQLite for indexable structured; jsonl for chronological log | Postgres + Redis (overkill for prototype) | Concurrent writes; mitigated by WAL mode | Append-only events by convention; manifest hash verification on resume |
| ModelClient | Anthropic API wrapper | Prompt caching; cost/token logging; thin abstraction for future swap | Direct API calls scattered | API drift; mitigated by typed client interface | Cache prefix marked; tokens + cost per call logged |
| Validation engine | Multi-layer correctness checks | "The script ran" is never a check | Single pytest exit code | False positives from weak assertions; mitigated by 6 author layers + 6 repair pieces | `ValidationReport` markdown + JSON sidecar |

## 4. Foundation attribution (verbatim — do not paraphrase)

> AgentForge's agent loop is implemented in `apps/api/src/agentforge/agent/loop.py` (~280 lines). The action/observation model, event-stream-driven state, and bounded loop with explicit termination are adapted from OpenHands' CodeAct agent design (https://github.com/All-Hands-AI/OpenHands, commit `3515cb085834623d9d0ef9d6977d1bcfa2b6eb57`, principally the files `openhands/controller/agent_controller.py` and `openhands/events/`). No OpenHands code was imported, vendored, or copied; the patterns were studied and reimplemented in a minimal form tailored to AgentForge's two finance workflows. Everything else — the wizard UI, workspace, sandbox wrapper, finance-domain tool registry, workflow orchestrator, validation system, fixtures, evals, and repair report — is original. A fallback option (Aider-as-library) was considered and held in reserve; it was not used.

This wording is canonical. It appears verbatim in `README.md`, `docs/adr/0001-open-source-foundation.md`, and `LICENSE`. Drift is a defect; `agentforge-architect` re-syncs.

## 5. Workspace directory structure

Each session has a directory under `${WORKSPACES_ROOT}/${session_id}/`:

```
${session_id}/
├── manifest.json        # {workflow, status, current_phase, current_step, budgets, file hashes, schema_version}
├── events.jsonl         # one event per line, append-only
├── uploads/             # user-uploaded files (immutable by convention after upload)
├── generated/           # AUTHOR: agent.py, rules.py, tests/, requirements.txt (git-init for diff tracking)
├── working/             # REPAIR: extracted + modified agent (git-init for diff tracking)
├── outputs/             # author-flow agent execution outputs (CSVs the agent produces)
│   └── _logs/           # large stdout/stderr overflow files (>1 MiB)
├── reports/             # validation_report.md, repair_report.md, README.md
└── archive.zip          # built on finalise; contains the above
```

File lifecycle rules:
- `uploads/` is write-once: the upload endpoint writes; no tool modifies.
- `generated/` and `working/` are git-tracked: every tool-write is a commit, allowing revert.
- `outputs/` and `reports/` are append-only by tool semantics: no tool overwrites a prior output file.
- `archive.zip` is built atomically on `finalise_session`.

Path discipline: every tool argument that names a path is resolved against `${workspace_path}` and rejected if the resolved path escapes (no `..` segments, no absolute paths). Enforced in `workspace_tools.py`.

## 6. State machine

### 6.1 Session statuses (terminal-aware)

| Status | Meaning | Resumable? |
|---|---|---|
| `created` | Row exists, workspace allocated | Yes |
| `running` | Agent loop active | (process state; not user-facing pause) |
| `paused_user` | Waiting for user answer | Yes |
| `paused_approval` | Waiting for user approval | Yes |
| `completed` | Terminal success | No (read-only) |
| `failed_budget` | Any budget hard cap hit | No |
| `failed_model` | Model errors unrecoverable | No |
| `failed_sandbox` | Sandbox unrecoverable | No |
| `failed_user_reject` | User declined critical approval | No |
| `failed_other` | Uncategorised infrastructure failure | No |
| `auto_archived` | Idle >24h in `paused_user`; workspace tarred, resumable read-only up to 7 days | Read-only |

### 6.2 Author phases

`author_template` → `author_upload` → `author_profile` → `author_describe` → `author_infer` → `author_qa` → `author_confirm` → `author_generate` → `author_review_diff` → `author_applied` → `author_run` → `author_validate` → `author_review` → `author_finalise` → terminal.

### 6.3 Repair phases

`repair_upload` → `repair_loaded` → `repair_problem` → `repair_triage` → `repair_confirm_summary` → `repair_reproduce` → `repair_need_info` (optional cycle) → `repair_diagnose` → `repair_propose` → `repair_review_patch` → `repair_apply` → `repair_validate` → `repair_report` → `repair_finalise` → terminal.

### 6.4 Tool-exposure phases (coarse)

| Workflow | Phase | Exposes |
|---|---|---|
| author | `info` (states: template through confirm) | `list_workspace`, `inspect_file`, `inspect_csv_schema`, `inspect_xlsx_schema`, `seed_template`, `ask_user` |
| author | `build` (states: generate through finalise) | adds: `write_file`, `apply_patch`, `run_python_script`, `run_pytest`, `validate_output`, `generate_validation_report`, `archive_workspace`, `finalise_session` |
| repair | `info` (states: upload through propose) | `list_workspace`, `inspect_file`, `summarise_agent_purpose`, `classify_problem`, `run_python_script` (reproduce), `run_pytest` (reproduce), `diagnose`, `propose_patch`, `ask_user` |
| repair | `fix` (states: apply through finalise) | adds: `apply_patch`, `run_python_script`, `run_pytest`, `validate_output`, `generate_repair_report`, `archive_workspace`, `finalise_session` |

### 6.5 Transition guards (enforced; illegal raises `WorkflowStateError`)

- No transition from a state to itself except where the workflow phase tables explicitly model loop-back (e.g., `author_qa` → `author_qa` does not occur; QA cycles use `paused_user` between cycles).
- No transition into `completed` without a recorded artifact (`generated/agent.py` for author; `repair_report.md` for repair).
- No transition into `repair_diagnose` without a `REPRODUCTION_RESULT` event in the log.
- No transition into `author_applied` or `repair_apply` without a recorded `APPROVAL_GRANTED` event for that step.
- No transition out of `paused_*` without a recorded user input event (`ANSWER_RECEIVED` or `APPROVAL_GRANTED`/`APPROVAL_DECLINED`).
- Max 3 clarifying-question cycles in `author_qa`.
- Max 2 patch-iteration cycles (`repair_review_patch` → `repair_diagnose`).

## 7. Budgets and limits (per session)

| Budget | Default | Hard cap behaviour |
|---|---|---|
| Input + output tokens | 150,000 | Pause; surface; user finalises or extends (single extend allowed) |
| Tool calls | 40 | Same |
| Agent loop steps | 25 (author) / 20 (repair) | Same |
| Wall-clock seconds | 1,500 (25 min) | Same |
| Single uploaded file | 25 MB | Reject at upload endpoint |
| Total uploads per session | 100 MB | Reject at upload endpoint |
| Generated file count | 20 | `write_file` / `apply_patch` rejected; surface |
| Rows profiled per file | 50,000 | Silent; SchemaTable notes "Profile based on first 50,000 rows" |
| Subprocess timeout (script) | 60 s | SIGKILL + record EXECUTION_FAILED `error_code=command_timeout` |
| Subprocess timeout (pytest) | 120 s | Same |
| Output capture (stdout/stderr) | 1 MiB per stream | Truncate with marker; overflow to `outputs/_logs/${step}.log` |

Warn-banner appears at 75% of hard cap for tokens, tool calls, steps, and wall.

## 8. Failure-mode catalogue (typed `error_code` enum)

| `error_code` | Detection | Stored evidence | Can continue? |
|---|---|---|---|
| `malformed_csv` | `inspect_csv_schema` raises | event + parser error + line/column | Yes (re-upload) |
| `unsupported_file_type` | upload endpoint MIME + extension check | rejection event | Yes |
| `file_too_large` | upload endpoint size check | rejection event | Yes |
| `upload_limit_exceeded` | upload endpoint total-size check | rejection event | Yes |
| `missing_required_columns` | `validate_output` finds absence | check-failed event | Yes (loop back to generate) |
| `ambiguous_schema` | profiler finds ambiguity (e.g., date format) | clarification-needed event | Yes (paused for user) |
| `tool_not_registered` | registry lookup fails | event + observation back to model | Yes (bounded by step cap) |
| `validation_loop_exhausted` | Pydantic re-prompt fails twice | terminal event | No (failed_model) |
| `generated_code_failed` | subprocess returns nonzero | event + stderr excerpt | Yes (loop back to generate) |
| `test_failed` | pytest returns failed > 0 | event + failed test names | Yes (loop back) |
| `command_timeout` | sandbox SIGKILL | EXECUTION_FAILED event | Yes (loop back) |
| `repair_cannot_reproduce` | pytest "no tests" + sample run passes | `repair_need_info` event | Yes (paused for user) |
| `budget_exhausted_tokens` | top-of-loop check | BUDGET_EXHAUSTED event | Yes (extend or finalise) |
| `budget_exhausted_tool_calls` | top-of-loop check | BUDGET_EXHAUSTED event | Yes |
| `budget_exhausted_steps` | top-of-loop check | BUDGET_EXHAUSTED event | Yes |
| `budget_exhausted_wall_time` | top-of-loop check | BUDGET_EXHAUSTED event | Yes |
| `budget_exhausted_file_count` | `write_file` / `apply_patch` check | rejection event | Yes (finalise) |
| `approval_declined` | user POSTs decline | APPROVAL_DECLINED event | Yes if mid-loop (bounded); No if terminal-step decline |
| `user_abandoned` | scheduled job, >24h idle in `paused_user` | SESSION_AUTO_ARCHIVED event | Read-only up to 7 days |
| `sandbox_crash` | subprocess unexpected exit | EXECUTION_FAILED event | Yes (retry once) |
| `unknown` | default classifier for uncaught | event + stack trace | Depends on phase |

Every failure event includes: `error_code` (typed), user-facing message, optional technical excerpt, current phase. The UI renders all failures through one `ErrorBanner` component with consistent shape.

## 9. Module boundaries and operator docs

Layer map, model routing, and cross-links: [`docs/architecture.md`](docs/architecture.md).

| Doc | Contents |
|---|---|
| [`docs/author-workflow.md`](docs/author-workflow.md) | Author state sequence and failure paths |
| [`docs/repair-workflow.md`](docs/repair-workflow.md) | Repair INFO/FIX phases and evidence gates |
| [`docs/contracts.md`](docs/contracts.md) | Contract-first flow; links to `CONTRACTS.md` |
| [`docs/validation.md`](docs/validation.md) | Four-tier Author validation |
| [`docs/sandbox-and-artifacts.md`](docs/sandbox-and-artifacts.md) | Sandbox invariants and reserved paths |
| [`docs/failure-modes.md`](docs/failure-modes.md) | Bounded repair, budgets, orphan recovery |

### Model routing (stage → config key)

| Stage | `Settings` field | Notes |
|---|---|---|
| Contract planning | `author_planning_model` | Schema-heavy JSON |
| Contract review | `author_review_model` | Separate critique pass |
| Code generation | `author_codegen_model` | `generated/agent.py` |
| Test generation | `author_testgen_model` | `generated/tests/` |
| Execution repair | `author_repair_model` | Bounded by `author_max_repair_attempts` |

Provider selection: `llm_provider` (`deepseek`, `ollama`, `anthropic`). Stage models override legacy defaults when set. No API keys in committed config — load from environment only.

## 10. References

- ADR-0001 Open-source foundation (`docs/adr/0001-open-source-foundation.md`)
- ADR-0002 Frontend choice (`docs/adr/0002-frontend.md`)
- ADR-0003 Backend / execution model (`docs/adr/0003-backend-execution.md`)
- ADR-0004 Workspace persistence (`docs/adr/0004-workspace-persistence.md`)
- ADR-0005 Tool registry (`docs/adr/0005-tool-registry.md`)
- ADR-0006 Approval model (`docs/adr/0006-approval-model.md`)
- ADR-0007 Validation strategy (`docs/adr/0007-validation-strategy.md`)
- ADR-0008 Fixture choice (`docs/adr/0008-fixture-choice.md`)
- ADR-0009 Claude Skills decomposition (`docs/adr/0009-skills-decomposition.md`)
- ADR-0010 Scope exclusions (`docs/adr/0010-scope-exclusions.md`)
- `CONTRACTS.md` — typed schemas and contracts at every module boundary.
- `WORKFLOWS.md` — exact author and repair workflow tables (steps, tools, files, state).
- `.claude/skills/agentforge-thesis-keeper/SKILL.md` — the 12 invariants enforced across this architecture.
- [`docs/architecture.md`](docs/architecture.md) — module boundary map and operator cross-links.
