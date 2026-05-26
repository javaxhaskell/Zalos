# ADR-0009 — Claude Skills decomposition

**Status:** accepted
**Date:** 2026-05-21

## Context

This project uses Claude Code with custom skills providing context continuity across many sequential build prompts. We need to choose the number of skills and their boundaries.

## Decision

**Six skills total.** Two advisory-only (always loaded for context). Four implementation-owning.

1. `agentforge-thesis-keeper` (advisory) — holds 12 load-bearing invariants; validates artifacts; PASS/BLOCKED verdicts.
2. `agentforge-architect` (advisory) — owns `ARCHITECTURE.md`, `CONTRACTS.md`, `WORKFLOWS.md`, all ADRs; signs off integration checkpoints; arbitrates structural questions.
3. `agentforge-backend` (implementation) — `apps/api/**`.
4. `agentforge-frontend` (implementation) — `apps/web/**`.
5. `agentforge-fixtures-and-evals` (implementation) — `templates/**`, `fixtures/**`, `evals/**`.
6. `agentforge-docs-and-demo` (implementation) — `README.md`, `DEPLOYMENT.md`, `RUNBOOK.md`, `TRANSCRIPT.md`, `docs/LOCAL_WALKTHROUGH.md`.

Skills live under `.claude/skills/agentforge-*` plus `.claude/skills/agentforge-thesis-keeper/`.

## Options considered

Scored across nine criteria (parallel build speed, architectural coherence, integration risk, file ownership clarity, duplicated work, end-to-end quality, debug-ability, defensibility, deadline fit):

| Option | Total |
|---|---|
| One monolithic Claude session | 14 |
| 3–4 broad skills | 21 |
| **5–7 focused skills** | **22 (selected at 6)** |
| 10–12 specialised skills | 17 |
| 17+ highly specialised | 14 |

## Rationale

- Solo sequential building: skills are context-continuity infrastructure, not parallelism enablers. The dependency graph still permits parallelism if a second builder joins, but the design optimises for one builder driving 12 prompts.
- 6 is the right balance: backend and frontend have meaningfully different stacks (Python/Pydantic vs TS/React) and benefit from distinct skill contexts; fixtures-and-evals is a focused activity that benefits from its own skill; docs-and-demo is late-stage and benefits from focused context for the curated transcript.
- Two advisory skills (thesis-keeper, architect) load at every prompt so the build cannot drift from the invariants or contracts.

## Consequences

- The earlier 17 AP Reconcile-specific skills were deleted (the take-home product is not AP Reconcile).
- Each implementation skill has a strict file-ownership whitelist and a forbidden scope list.
- `agentforge-architect` is the only skill that writes ADRs and edits `ARCHITECTURE.md` / `CONTRACTS.md` / `WORKFLOWS.md`.
- `agentforge-thesis-keeper` is the only skill that validates artifacts against the 12 invariants and produces PASS / BLOCKED verdicts.
- A skills README at `.claude/skills/README.md` documents the dependency graph, build sequence, integration checkpoints, shared contracts, and anti-drift rules.

## Reversal condition

- Add more skills if a second builder joins (parallelism becomes valuable).
- Collapse to 4 skills (merge backend + frontend into one "builder", merge fixtures-and-evals into "builder") only if a future build is dramatically smaller in scope.
