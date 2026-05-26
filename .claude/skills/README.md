# AgentForge Skills System

Six skills for building the Zalos take-home: **AgentForge** — a finance-user-facing app for authoring and repairing Python finance agents from Excel/CSV samples.

Two skills are advisory only (always loaded for context). Four are implementation-owning.

**Operating thesis (load-bearing across every skill):**

> The LLM proposes typed plans and edits. The backend deterministically validates, sandboxes, runs, and records. The model never touches the user's workspace without recorded approval. Tools are a typed registry; sandboxes are bounded; audit is append-only. Synthetic data only.

Every implementation skill loads `agentforge-thesis-keeper` first and cites invariants by number when justifying decisions.

---

## The 6 skills

| # | Skill | Role | Type | Activates in build prompts |
|---|---|---|---|---|
| 1 | [`agentforge-thesis-keeper`](agentforge-thesis-keeper/SKILL.md) | Holds 12 invariants; validates artifacts | Advisory | Loaded at the top of every prompt |
| 2 | [`agentforge-architect`](agentforge-architect/SKILL.md) | Owns ARCHITECTURE.md, CONTRACTS.md, WORKFLOWS.md, ADRs; signs off integration checkpoints | Advisory | Phase 1 + every checkpoint |
| 3 | [`agentforge-backend`](agentforge-backend/SKILL.md) | Implements `apps/api/**` (FastAPI, agent loop, tool registry, sandbox, persistence, validation engine) | Implementation | Prompts 1, 3, 4, 5, 6, 10, 11 |
| 4 | [`agentforge-frontend`](agentforge-frontend/SKILL.md) | Implements `apps/web/**` (Next.js wizard, 5 screens, Playwright smoke) | Implementation | Prompts 1, 7, 8, 9 |
| 5 | [`agentforge-fixtures-and-evals`](agentforge-fixtures-and-evals/SKILL.md) | Owns `templates/**`, `fixtures/**`, `evals/**` | Implementation | Prompts 2, 10 |
| 6 | [`agentforge-docs-and-demo`](agentforge-docs-and-demo/SKILL.md) | Owns README, DEPLOYMENT, RUNBOOK, TRANSCRIPT, fallback video | Implementation | Prompt 12 |

---

## Dependency graph

```
agentforge-thesis-keeper  (loaded by every other skill; never blocks)
       │
       ▼
agentforge-architect  (writes ADRs, contracts, workflows; consulted at every checkpoint)
       │
       ├──► agentforge-fixtures-and-evals
       │
       ├──► agentforge-backend ──► OpenAPI snapshot
       │                                │
       │                                ▼
       ├──► agentforge-frontend  (reads OpenAPI snapshot + TS mirrors)
       │
       └──► agentforge-docs-and-demo  (reads everything; produced last)
```

---

## Sequential build map (12 prompts)

Each prompt is requested individually; each builds on the prior one. Verify acceptance criteria before moving on.

| # | Objective | Active skills |
|---|---|---|
| 1 | Foundation + repo skeleton + frozen contracts + 10 ADRs | architect + backend + frontend (scaffold) |
| 2 | Synthetic fixtures + golden outputs + author template | fixtures-and-evals |
| 3 | Session + workspace + event log model + endpoints | backend |
| 4 | Tool registry + execution layer + read tools | backend |
| 5 | Agent loop + author workflow backend end-to-end | backend |
| 6 | Repair workflow backend end-to-end | backend |
| 7 | UI workflow shell + shared components | frontend |
| 8 | Author UI integration | frontend |
| 9 | Repair UI integration | frontend |
| 10 | Validation reports + artifact packaging + eval runner | backend + fixtures-and-evals |
| 11 | Resume + budgets + fault handling | backend |
| 12 | Documentation + transcript + fallback recording | docs-and-demo |

Each prompt has prerequisites, file whitelist, acceptance criteria, and verification step defined in the final decision document §14.

---

## Integration checkpoints

After every implementation prompt:

1. The owning skill produces acceptance-criteria evidence (test run, screenshot, OpenAPI diff).
2. `agentforge-thesis-keeper` validates against the 12 invariants → PASS or BLOCKED.
3. `agentforge-architect` validates against contracts and ADRs → READY_FOR_NEXT_PROMPT or BLOCKED.
4. Next prompt fires only on green from both.

---

## Shared contracts (locked at end of Prompt 1)

All implementation skills obey these contracts. Changes require an ADR.

1. **Session state schema** (Pydantic + TS mirror) — `Session`, `WorkspaceEvent`, `UploadedFile`, `Artifact`, `ApprovalRequest`, `ApprovalDecision`, `ToolCall`, `ToolObservation`.
2. **Event log schema** — JSONL line shape and the event-kind enum.
3. **Workspace directory structure** — exact paths under `${workspace_root}/${session_id}/`.
4. **Tool registry definitions** — `ToolDefinition` shape; per-tool entries.
5. **Workflow phase names** — locked enum values for author and repair phases.
6. **Artifact paths** — where each artifact type lives within the workspace.
7. **Validation report format** — markdown structure + JSON sidecar shape.
8. **User-facing status format** — finance-user UX language map (verbatim from decision §9).

---

## Centralised decisions (no individual skill may change independently)

- Foundation attribution wording (verbatim from decision document §4).
- Workflow phase names.
- Tool registry shape and risk levels.
- Event log schema.
- Workspace directory structure.
- File paths in `ARCHITECTURE.md`.
- Budgets and limits.
- Failure modes' typed `error_code` enum.
- The set of ADRs.

---

## Anti-drift rules

1. No skill invents a Pydantic type. All types come from `apps/api/src/schemas/`; new types are filed as issues to `agentforge-architect`.
2. No skill renames a workflow phase. Names are locked at Prompt 1.
3. No skill changes the workspace directory structure. Deviations require an ADR.
4. No skill invents new event kinds. The enum is in `CONTRACTS.md`.
5. No skill bypasses the approval gate. Write tools always require approval unless an ADR opts them out and the tool's docstring cites that ADR.
6. No skill writes marketing claims. All user-facing language goes through `agentforge-docs-and-demo`, which is forbidden from marketing language.
7. All foundation-attribution language is verbatim from `ARCHITECTURE.md` (which is verbatim from decision §4).

---

## How to invoke

Skills auto-trigger from their description frontmatter. To invoke explicitly:

- **At the start of a phase:** paste the skill's "Ready-to-copy execution prompt" from its SKILL.md into a fresh Claude Code session.
- **Mid-build:** refer to the skill by name in conversation ("ask `agentforge-backend` to add the approval-pause path").
- **Validation:** invoke `agentforge-thesis-keeper` to validate any artifact against the 12 invariants.
- **Schema or ADR question:** invoke `agentforge-architect`.

---

## What this system is not

- Not a parallel build orchestrator for multiple humans. Designed for solo sequential building with skills as context-continuity infrastructure across prompts. If a second builder joins, the dependency graph above shows where parallelism is safe (after Prompt 1's contracts freeze).
- Not a replacement for ADRs. Skills enforce decisions; ADRs record them. When the two diverge, the ADR is the source of truth and the skill is updated.
- Not specific to AP Reconcile. Earlier AP Reconcile-specific skills were deleted when the take-home prompt clarified the actual product (agent-authoring tool, not a specific finance agent).

---

## Reduced-submission path

If the build hits time pressure on Sunday: ship Prompts 1, 2, 3, 4, 5, 7, 8, 10, 11, 12. This delivers the author workflow end-to-end and documents the repair workflow as "completed-design implementation deferred." Prompt 9 (repair UI) is the natural cut because Prompt 6's repair backend can be exposed via a minimal admin surface; the wizard UI for repair is the polish layer.

---

## File references

- `agentforge-thesis-keeper/SKILL.md` — the 12 invariants and validation protocol.
- `agentforge-architect/SKILL.md` — ADR set, contract authoring, checkpoint protocol.
- `agentforge-backend/SKILL.md` — backend implementation rules, the agent loop spec, sandbox runner spec.
- `agentforge-frontend/SKILL.md` — Next.js wizard spec, 5 screens, component list.
- `agentforge-fixtures-and-evals/SKILL.md` — bank-categoriser template, invoice-aging fixture, eval runner.
- `agentforge-docs-and-demo/SKILL.md` — README, foundation attribution, transcript curation, fallback video.

The final decision document (loaded in the user's conversation context) is the canonical source for product scope, workflows, tool registry, fixtures, validation strategy, budgets, scope boundaries, ADR list, build sequence, and decision summary.
