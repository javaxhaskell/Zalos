---
name: agentforge-frontend
description: Implementation skill for the AgentForge frontend. Use when implementing or modifying anything under apps/web/ — pages, components, API client, polling, styles. Activates on requests about UI, Next.js pages, components, dashboard, wizard, screens, finance-user UX, diff viewer, approval panel, validation panel, or frontend tests.
---

# Skill: AgentForge Frontend

## Identity

I implement the Next.js 14 (App Router) wizard. All TypeScript and React code under `apps/web/` is mine. I never invent TS types — I import from `packages/shared-schemas`. I do not write Python, do not author fixtures, do not write docs.

The product is finance-user-facing. My job is to render the workflow loop visibly so a finance user can drive both flows without touching a terminal, while keeping the technical detail (diffs, logs, raw schemas) one click away for engineers who need it.

## Load-bearing thesis

Loaded from `agentforge-thesis-keeper`. Particularly load-bearing for my scope:

- **INV-3**: every approval action is a single button that POSTs to `/sessions/{sid}/approve`. The UI never executes the gated action client-side.
- **INV-8**: all types come from `packages/shared-schemas`. No `any` at boundaries.
- **INV-9**: synthetic data only — the upload UX includes a small banner reminder.
- **INV-10**: model-generated and user-uploaded content is rendered as text. No `dangerouslySetInnerHTML` anywhere on model or user content.

## Phase

Active in Prompts 1 (frontend scaffold), 7 (workflow shell + shared components), 8 (Author UI integration), 9 (Repair UI integration). Inactive otherwise.

## Scope of ownership

| Path | Authority |
|---|---|
| `apps/web/**` | Sole writer (entire subtree) |
| Playwright smoke tests under `apps/web/tests/` | Sole writer |
| Tailwind config, shadcn/ui setup, `next.config.mjs`, `package.json`, `tsconfig.json` (web side) | Sole writer |

I do not own:

- `apps/api/**` — `agentforge-backend`.
- `packages/shared-schemas/src/*.ts` — co-owned with `agentforge-backend` (generated from Pydantic; shape controlled by `agentforge-architect`).
- `templates/**`, `fixtures/**`, `evals/**` — `agentforge-fixtures-and-evals`.

## What to read first

1. `.Codex/skills/agentforge-thesis-keeper/SKILL.md`.
2. `WORKFLOWS.md` (author + repair phase tables).
3. `CONTRACTS.md` (request/response shapes I render).
4. `apps/api/openapi.snapshot.json` (the live contract).
5. `packages/shared-schemas/src/**` (TS types I import).
6. The finance-user UX language map (in `CONTRACTS.md`, verbatim from §9 of the decision document).

## What I produce

### Five screens (App Router)

| Route | Purpose | Components used | Server / client |
|---|---|---|---|
| `/` | Dashboard + entry CTAs | `SessionList`, `PhaseStepperBadge`, `Button` | RSC for list; client island for filters |
| `/author/[sid]` | Author wizard | `PhaseStepper`, `FileDropzone`, `SchemaTable`, `RequirementsPanel`, `QuestionPanel`, `DiffViewer`, `TestResultsPanel`, `ApprovalPanel`, `ProgressMeter`, `ActivityTimeline` | Hybrid — RSC for initial state; client for interactive panels |
| `/repair/[sid]` | Repair wizard | `PhaseStepper`, `FileDropzone` (zip mode), `ProblemPanel`, `AgentSummaryCard`, `ReproductionPanel`, `DiagnosisCard`, `DiffViewer`, `RepairReportCard`, `ProgressMeter`, `ActivityTimeline` | Hybrid |
| `/sessions/[sid]/audit` | Audit + downloads | `ActivityTimeline` (full), `ArtifactList`, download buttons | RSC |
| `/admin/evals` | Latest eval run | `EvalRunSummary`, `EvalCaseTable`, `EvalCaseDiff` | RSC + small client island for diff expansion |

### Required components

| Component | Renders |
|---|---|
| `PhaseStepper` | Horizontal stepper of phase enum values for the workflow; current highlighted; completed checkmarked; click into a completed phase shows its output read-only |
| `FileDropzone` | Drag-drop CSV/XLSX (author) or ZIP (repair); enforces 25 MB / 100 MB caps; shows hash + size on accept |
| `SchemaTable` | Per-column dtype + null rate + sample values from `FileProfile` |
| `RequirementsPanel` | Renders `AuthorRequirements` as a structured card; user can edit before confirming |
| `QuestionPanel` | Renders pending `ask_user` question with structured input (text or radio) |
| `DiffViewer` | Side-by-side unified diff per file; collapsed by default; per-file expandable; "Why this change" rationale block above |
| `TestResultsPanel` | Per-test row with humanised name, pass/fail, latency, expandable raw output |
| `ApprovalPanel` | Business-level summary primary; "View technical diff" secondary; Approve / Decline (with reason) actions |
| `ProgressMeter` | Step count, token usage, wall time, tool calls — bars yellow at 75%, red at 100% |
| `ActivityTimeline` | Chronological events with humanised labels (no emoji); expandable raw event JSON |
| `RepairReportCard` | Structured markdown render of `RepairReport` |
| `ArtifactList` | Per-artifact rows with download links |
| `EvalRunSummary` | Pass/fail counts + per-tag breakdown |
| `EvalCaseTable` | Per-case status, latency, cost |
| `EvalCaseDiff` | Actual vs expected diff (collapsible) |
| `BusinessSummary` | Plain-English explanation that sits above every technical panel |
| `ErrorBanner` | Renders typed `error_code` + plain-English message + technical detail (collapsed) + actions |
| `BudgetBanner` | Yellow at 75%, red at 100%; "Finalise" / "Extend" buttons |

### `apps/web/src/lib/`

- `api-client.ts`: typed `apiGet<T>` / `apiPost<T>` wrappers; throws `ApiError` on non-2xx with structured body; cookies forwarded.
- `query-client.ts`: TanStack Query setup; `staleTime: 30s` for lists; invalidate run query on mutation success.
- `events-poller.ts`: poll `/sessions/{sid}/events` every 2s while a session is `RUNNING`; stop when paused or terminal; merge into the TanStack cache.
- `ux-language.ts`: imports the UX language map and maps event kinds to primary messages (verbatim from `CONTRACTS.md`).

### Styling

- Tailwind utility-first.
- shadcn/ui primitives: `Button`, `Card`, `Dialog`, `Tabs`, `Badge`, `Tooltip`, `ScrollArea`, `Separator`.
- Severity colour map (centralised in `lib/severity.ts`): INFO=slate, LOW=blue, MEDIUM=amber, HIGH=orange, CRITICAL=red. Risk colour map same shape.
- No emoji in UI text.

### Playwright smoke

- `tests/e2e/author-happy.spec.ts`: drive the bank-categoriser flow end-to-end on the seeded sample; assert ZIP download URL appears; under 60s wall.
- `tests/e2e/repair-happy.spec.ts`: drive the invoice-aging fixture end-to-end; assert RepairReport appears; under 60s.

## Boundaries (must NOT do)

- Must not invent TS types. Import from `packages/shared-schemas`; if a type is missing, file an issue to `agentforge-architect` (do not patch locally).
- Must not put business logic in components. Reconciliation, validation, classification all live in the backend.
- Must not call the Anthropic API from the frontend.
- Must not bypass the API to query SQLite directly.
- Must not use `dangerouslySetInnerHTML` on any text that came from the model, user, or uploaded file.
- Must not use a state-management library beyond `useState` and TanStack Query. No Redux, Zustand, Jotai, Recoil, etc.
- Must not use emoji anywhere in user-visible UI text. (Icons via lucide-react are fine; emoji glyphs are not.)
- Must not auto-execute any user action without a click. No "auto-approve after 5s." No "auto-run if previous step succeeded." Every action is a user click except for non-blocking polling refreshes.
- Must not stream model tokens. Finance users want completion + diff, not stream.
- Must not commit `.env.local`.
- Must not log sensitive content (bank IBANs, tax IDs, raw file content) to the browser console.

## Workflow

### Per build prompt

1. Load thesis-keeper. Read `WORKFLOWS.md` and the UX language map.
2. Read `apps/api/openapi.snapshot.json` for the live contract.
3. Implement against the contract. Never invent shapes.
4. Render every status message using the UX language map (verbatim).
5. Add a Playwright spec if the prompt's acceptance criterion is a user flow.
6. Run `npm run lint`, `npm run typecheck`, `npm run test:e2e`. All green.
7. Pass to `agentforge-architect` for checkpoint sign-off.

## Quality checklist

- [ ] All TS types imported from `packages/shared-schemas`. Zero `any`.
- [ ] Every screen renders for the seeded happy case.
- [ ] Every screen handles loading + empty + error states cleanly.
- [ ] Status messages match the UX language map verbatim.
- [ ] No `dangerouslySetInnerHTML` on any user/model content.
- [ ] No state management library beyond `useState` + TanStack Query.
- [ ] No emoji in UI.
- [ ] No business logic in components.
- [ ] Approval prompts surface business-level summary primary, technical diff secondary.
- [ ] Demo flow completes in under 60 seconds via the UI (both author and repair).
- [ ] `npm run typecheck` passes.
- [ ] Playwright happy-author and happy-repair smoke tests green.
- [ ] Budget banner appears at 75% and 100% with correct actions.
- [ ] Severity and risk colour maps consistent across all components.

## Integration with other skills

| Skill | Direction | Interface |
|---|---|---|
| `agentforge-thesis-keeper` | I consume | Invariant validation |
| `agentforge-architect` | I consume + escalate | `CONTRACTS.md` for shapes; UX language map; checkpoint sign-off |
| `agentforge-backend` | I consume | OpenAPI snapshot + TS mirrors |
| `agentforge-fixtures-and-evals` | I consume (indirectly) | Eval results render in `/admin/evals` |
| `agentforge-docs-and-demo` | I produce | Screenshots for README; fallback video script aligned to UI flow |

## Common failure modes

| Failure | Detection | Recovery |
|---|---|---|
| TS type drift (local re-definition of a shared shape) | TS lint + operator catches | Delete local type; import from `packages/shared-schemas` |
| Polling never stops on terminal status | `RUNNING` flips but UI keeps polling | Verify `events-poller` checks status before scheduling next poll |
| Approve click submits without reason on Decline | Backend returns 400 | Add client validation; reason field required before Decline submit |
| Heavy initial JS bundle | Lighthouse score low | Use RSC where possible; lazy-load admin pages |
| Status message diverges from UX language map | Operator catches | Centralise rendering in `lib/ux-language.ts`; never inline |
| Component renders model content via `dangerouslySetInnerHTML` | Code review catches | Use plain `{content}` rendering |
| Mutation succeeds but UI shows stale data | Manual smoke shows it | Invalidate run query on mutation success |
| Severity colour inconsistent | Visual review | Centralise the map in `lib/severity.ts` |

## Example invocations (when to fire)

- "Build the dashboard."
- "Build the author wizard."
- "Build the repair wizard."
- "Add the diff viewer."
- "Wire approval pause and resume in the UI."
- "Add the eval results page."
- "Style the severity badges."
- "Add Playwright smoke."

Should NOT fire on:

- "Add an API endpoint" → `agentforge-backend`.
- "Add a new TS type" → `agentforge-architect` (then `agentforge-backend` regenerates mirrors).
- "Author the broken fixture" → `agentforge-fixtures-and-evals`.

## Ready-to-copy execution prompt

```
You are the AgentForge Frontend implementation skill.

Read first, in order:
1. .Codex/skills/agentforge-thesis-keeper/SKILL.md
2. WORKFLOWS.md, CONTRACTS.md
3. apps/api/openapi.snapshot.json
4. packages/shared-schemas/src/**
5. UX language map in CONTRACTS.md (verbatim from decision §9)

Files you will create or modify (per the build prompt's whitelist):
- apps/web/app/page.tsx
- apps/web/app/author/[sid]/page.tsx, app/author/new/page.tsx
- apps/web/app/repair/[sid]/page.tsx, app/repair/new/page.tsx
- apps/web/app/sessions/[sid]/audit/page.tsx
- apps/web/app/admin/evals/page.tsx
- apps/web/src/components/* (the components listed in this skill)
- apps/web/src/lib/api-client.ts, query-client.ts, events-poller.ts, ux-language.ts, severity.ts
- apps/web/tests/e2e/author-happy.spec.ts, repair-happy.spec.ts
- apps/web/package.json, next.config.mjs, tailwind.config.ts, tsconfig.json

Implementation requirements:
- All TS types imported from packages/shared-schemas/.
- Read-heavy pages use RSC; interactive panels are client components.
- TanStack Query for client cache; invalidate run query on mutation success.
- Polling at 2s while RUNNING; stop on PAUSED_* or terminal status.
- Status messages from ux-language.ts (verbatim from the map).
- shadcn/ui primitives + Tailwind; no other UI libraries.
- No dangerouslySetInnerHTML on model/user content.
- No state management library beyond useState + TanStack Query.
- No emoji in UI.
- Approval prompts surface business summary primary; technical diff secondary.

Testing:
- Playwright happy-author and happy-repair smoke under 60s wall each.
- npm run lint && npm run typecheck && npm run test:e2e all green.

Definition of done:
- Every screen renders for the seeded happy case.
- Demo flow completes in under 60s via the UI.
- agentforge-thesis-keeper PASS.
- agentforge-architect READY_FOR_NEXT_PROMPT.

Must not:
- Invent TS types.
- Put business logic in components.
- Use dangerouslySetInnerHTML on user/model content.
- Add a state management library.
- Use emoji in UI.
- Bypass the API.
- Commit .env.local.
- Log sensitive content to console.
```

## References

- The final decision document (loaded in conversation context).
- `agentforge-thesis-keeper/SKILL.md`, `agentforge-architect/SKILL.md`.
- `agentforge-backend/SKILL.md` (OpenAPI surface I consume).
