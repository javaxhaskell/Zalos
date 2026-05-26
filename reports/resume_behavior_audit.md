# Resume Behavior Audit

**Date:** 2026-05-25  
**Scope:** AgentForge Author and Repair sessions — what happens when a user leaves and returns later  
**Method:** Code inspection + existing tests/fixtures/workspace artifacts (no LLM workflow runs)  
**Thesis (INV-6):** Event log is append-only; backend is authoritative; model never mutates workspace without recorded approval.

---

## Executive summary

| Field | Value |
|---|---|
| **Audit path** | `reports/resume_behavior_audit.md` |
| **Overall status** | **AMBER** |
| **Exact behavior** | Sessions are **durable and reopenable** via URL (`/author/[sid]`, `/repair/[sid]`) or dashboard. State is reconstructed from SQLite + `events.jsonl` + workspace files. **Pause gates** (clarification, approval) resume via `/answer` or `/approve` then `/run`. **Terminal** sessions (completed / failed) reopen read-only with artifacts and mitigation. **Retry** on failed sessions is a **new orchestrator execution** with prior events in context — not arbitrary step-level continuation. |
| **Wording honest?** | **Partially.** Dashboard says “save and return later” (mostly true). Component is named `ResumeBanner` but shows “Next step: …”, not “Resume”. API field `can_resume` is always `false`. `retry_same_inputs` is labeled correctly in copy but **not wired as a clickable action** in the UI. |
| **Biggest gap** | **No true in-flight step-level resume.** Backend restart (or orphan `RUNNING` rows) marks sessions `failed_other` / `workflow_interrupted`; user must POST `/run` again (full flow re-execution). Secondary gap: **workflow description before first `/run` is React state only** — not persisted if the user leaves during `created`. |
| **Minimal fix** | Docs + small UX honesty fixes (see Phase 5). No lifecycle refactor required for submission honesty. |
| **Submission-ready?** | **Yes (AMBER)** — requirement is satisfied if resume semantics are described honestly in README/docs; do not claim step-level continuation. |

---

## Phase 1 — Session state model

### Backend persistence

| Layer | Location | Role on resume |
|---|---|---|
| **SQLite `sessions` row** | `apps/api/src/agentforge/persistence/models.py` | Authoritative for `status`, `current_phase`, `current_step`, budgets, `terminal_error_code`, `last_event_id`, archive/delete flags |
| **`events.jsonl`** | `${workspace}/events.jsonl` | Append-only audit + UI reconstruction (INV-6). Polling via `GET /sessions/{id}/events?after=` |
| **`manifest.json`** | `${workspace}/manifest.json` | Mirror of session row + `file_hashes` + `completion` metadata at terminalisation |
| **Workspace tree** | `uploads/`, `generated/`, `working/`, `outputs/`, `reports/` | Uploaded inputs, generated code, repair working copy, outputs, reports |
| **`archive.zip`** | `${workspace}/archive.zip` | Built on demand by tool or `GET /sessions/{id}/archive.zip` |

**Status enum** (`SessionStatus` in `apps/api/src/agentforge/schemas/common.py`):

`created` → `running` → (`paused_user` | `paused_approval`) → terminal (`completed`, `failed_*`, `auto_archived`)

### Key API endpoints (shared Author/Repair)

| Endpoint | Resume relevance |
|---|---|
| `POST /sessions` | Create session + workspace |
| `GET /sessions`, `GET /sessions/{id}` | Dashboard + wizard hydration; orphan `RUNNING` recovery on GET |
| `GET /sessions/{id}/events` | Event tail for UI (2s poll while active) |
| `POST /sessions/{id}/run` | Start/restart orchestrator (202). Blocked if `COMPLETED` or `RUNNING` with active task |
| `POST /sessions/{id}/answer` | Persist `answer_received`; next `/run` continues loop |
| `POST /sessions/{id}/approve`, `/reject` | Persist approval decision; next `/run` continues loop (INV-3) |
| `POST /sessions/{id}/cancel` | Terminal `failed_other` / user abandoned |
| `POST /sessions/{id}/finalise` | HTTP completion gate |
| `POST /sessions/{id}/archive`, `/restore`, `DELETE` | Dashboard lifecycle |
| `GET /audit/export/{id}` | Full chain + manifest |
| `GET /sessions/{id}/archive.zip` | Lazy archive build + download |
| `GET /sessions/{id}/artifacts/{path}` | Per-file artifact download |
| `POST /sessions/{id}/load_fixture/{name}` | Repair demo fixture → `working/` |
| `POST /sessions/{id}/files` | Author/Repair uploads |

**Retry semantics** (`docs/FAULT_TOLERANCE_MODEL.md`, `sessions.py` docstring):

- `paused_user` / `paused_approval`: answer or approve, then `/run`
- Terminal `failed_*`: `/run` allowed — **re-executes flow** with prior events in model context
- `can_resume: false` always on `failure_mitigation` (`user_mitigation.py`)

### Frontend

| Route | Behavior |
|---|---|
| `/` | Dashboard; `RecentSessions` links to `/author/{id}` or `/repair/{id}` |
| `/author/[sid]` | Wizard derived from `useSessionState` (polls session + events) |
| `/repair/[sid]` | Same pattern |
| `/sessions/[sid]/audit` | Audit export + live event stream |

**No `localStorage`.** Only `sessionStorage` use: expense review row decisions (`expense-review-queue.tsx`) — browser-tab scoped, not server-backed.

**`ResumeBanner`** (`resume-banner.tsx`): shows “Next step: …” from `session.status`, not “Welcome back” or “Progress saved”.

**Polling** (`use-session-state.ts`): 2s while `running` or `paused_*`; 5s otherwise; stops on terminal statuses.

---

## Phase 2 — Scenario trace (A–I)

Evidence drawn from: `test_sessions_lifecycle.py`, `test_resume_after_restart.py`, `test_runner_orphan_recovery.py`, `test_archive.py`, `test_author_date_clarification.py`, `test_repair_run_endpoint.py`, `.workspaces-deepseek-rerun/.../manifest.json`, and wizard source.

### Scenario table

| Scenario | Current behaviour | Evidence | User-visible result | Limitation |
|---|---|---|---|---|
| **A. Author before generation** | Uploaded files **persist** on disk + `file_uploaded` events + manifest hashes. Workflow **description is not persisted** until POST `/run` sends `user_message`. Template picker state is React-only. | `test_sessions_lifecycle.py` (upload); `author/[sid]/page.tsx` InputStage `useState(description)` | Returning user sees `created` + uploaded files in InputStage; **empty description textarea** | Must re-enter prompt (or re-select reference) before Start |
| **B. Author during generation** | Orchestrator runs in **background task** (`spawn_flow_task`). Progress = events + session row. UI polls on return. | `sessions.py` `/run`; `use-session-state.ts` | Running stage + timeline cards from events | If API **restarts** while `running`, session → `failed_other` / `workflow_interrupted` (`test_runner_orphan_recovery.py`, `lifespan.py`) |
| **C. Author completed** | Row `completed`; manifest `completion` populated; outputs on disk | `.workspaces-deepseek-rerun/.../manifest.json`; `FinanceSummaryCard`, `OutputPreviewCard` | Outputs, validation summary, download links, audit | `POST /run` returns 409 — cannot re-run without new session |
| **D. Author failed** | Terminal status + `failure_mitigation` on GET; events + partial artifacts preserved; archive may exist | `user_mitigation.py`; `FailureCard`; workspace sample manifest with `failed_other` + artifacts | Failure card, evidence list, audit link, archive download when present | **Retry = full re-execution** via `/run`, not mid-stage resume. `retry_same_inputs` button **not clickable** in UI |
| **E. Author clarification** | `question_asked` in events; status `paused_user`. Answer → `answer_received`. UI auto-calls `/run` after answer | `test_resume_after_restart.py` (answer after restart); `user-question-panel.tsx`; `author_date_clarification.py` | Question panel on return; answer persisted in event log | User must submit answer (and UI triggers `/run`). Unclear answers may re-pause |
| **F. Repair before run** | `load_fixture` or ZIP upload → `working/` + `fixture_loaded` / upload events | `test_repair_run_endpoint.py` | Input stage shows fixture loaded from events | Problem-report text in React state until `/run` (same as Author description) |
| **G. Repair completed** | Same as C; repair-specific cards from events (`RepairReportCard`, etc.) | Repair wizard CompletedStage; `repair-summary-card.tsx` | Before/after summaries, repair report, archive download | Read-only reopen |
| **H. Repair failed** | Same as D; `repair_cannot_reproduce` has dedicated copy | `FailureCard`, `REPAIR_CANNOT_REPRODUCE_COPY` | Failure reason + mitigation | Logs preserved in workspace; retry = full rerun |
| **I. Deleted session** | Soft delete: `GET /sessions/{id}` → 404; hard delete removes workspace | `session_store.get_session` checks `deleted_at`; `recent-sessions.tsx` deleted tab (no link) | Wizard shows **ErrorBanner** (404); dashboard “Recently deleted” tab | No in-wizard “this session was deleted” recovery; restore via dashboard only |

---

## Phase 3 — Scenario table and eighteen questions

### Scenario table (A–I)

| Scenario | Current behaviour | Evidence | User-visible result | Limitation |
|---|---|---|---|---|
| **A. Author before generation** | Uploaded files **persist** on disk + `file_uploaded` events + manifest hashes. Workflow **description is not persisted** until POST `/run` sends `user_message`. Template picker state is React-only. | `test_sessions_lifecycle.py` (upload); `author/[sid]/page.tsx` InputStage `useState(description)` | Returning user sees `created` + uploaded files in InputStage; **empty description textarea** | Must re-enter prompt (or re-select reference) before Start |
| **B. Author during generation** | Orchestrator runs in **background task** (`spawn_flow_task`). Progress = events + session row. UI polls on return. | `sessions.py` `/run`; `use-session-state.ts` | Running stage + timeline cards from events | If API **restarts** while `running`, session → `failed_other` / `workflow_interrupted` (`test_runner_orphan_recovery.py`, `lifespan.py`) |
| **C. Author completed** | Row `completed`; manifest `completion` populated; outputs on disk | `.workspaces-deepseek-rerun/.../manifest.json`; `FinanceSummaryCard`, `OutputPreviewCard` | Outputs, validation summary, download links, audit | `POST /run` returns 409 — cannot re-run without new session |
| **D. Author failed** | Terminal status + `failure_mitigation` on GET; events + partial artifacts preserved; archive may exist | `user_mitigation.py`; `FailureCard`; workspace sample manifest with `failed_other` + artifacts | Failure card, evidence list, audit link, archive download when present | **Retry = full re-execution** via `/run`, not mid-stage resume. `retry_same_inputs` button **not clickable** in UI |
| **E. Author clarification** | `question_asked` in events; status `paused_user`. Answer → `answer_received`. UI auto-calls `/run` after answer | `test_resume_after_restart.py` (answer after restart); `user-question-panel.tsx`; `author_date_clarification.py` | Question panel on return; answer persisted in event log | User must submit answer (and UI triggers `/run`). Unclear answers may re-pause |
| **F. Repair before run** | `load_fixture` or ZIP upload → `working/` + `fixture_loaded` / upload events | `test_repair_run_endpoint.py` | Input stage shows fixture loaded from events | Problem-report text in React state until `/run` (same as Author description) |
| **G. Repair completed** | Same as C; repair-specific cards from events (`RepairReportCard`, etc.) | Repair wizard CompletedStage; `repair-summary-card.tsx` | Before/after summaries, repair report, archive download | Read-only reopen |
| **H. Repair failed** | Same as D; `repair_cannot_reproduce` has dedicated copy | `FailureCard`, `REPAIR_CANNOT_REPRODUCE_COPY` | Failure reason + mitigation | Logs preserved in workspace; retry = full rerun |
| **I. Deleted session** | Soft delete: `GET /sessions/{id}` → 404; hard delete removes workspace | `session_store.get_session` checks `deleted_at`; `recent-sessions.tsx` deleted tab (no link) | Wizard shows **ErrorBanner** (404); dashboard “Recently deleted” tab | No in-wizard “this session was deleted” recovery; restore via dashboard only |

### Question ↔ scenario index

| Q# | Topic | Primary scenarios |
|---|---|---|
| 1 | Author persistence | A, B, C, D, E |
| 2 | Repair persistence | F, G, H |
| 3 | Reopen completed | C, G |
| 4 | Reopen failed | D, H |
| 5 | Reopen clarification | E |
| 6 | Step-level resume | B, D, H |
| 7 | Retry semantics | D, H |
| 8 | UI wording | A, D, dashboard |
| 9 | Artifact download | C, D, G, H |
| 10 | Archive-on-demand | C, D, G, H |
| 11 | Event log authority | all |
| 12 | Page refresh | B, E |
| 13 | Backend restart | B |
| 14 | Durable disk state | A–H |
| 15 | Transient state | A, F |
| 16 | Review decisions | C (expense review queue) |
| 17 | Take-home sufficient? | all |
| 18 | Minimal improvement | all |

### Eighteen questions

1. **What exactly persists for Author sessions?**  
   SQLite row, full `events.jsonl`, `manifest.json`, workspace files (`uploads/`, `generated/`, `outputs/`, `reports/`), optional `archive.zip`, approval/decision events after gates fire. **`user_message` (workflow description) only after first `/run`** (in events / model context), not while still `created`.

2. **What exactly persists for Repair sessions?**  
   Same stack. Additionally `working/` agent tree after fixture load or ZIP upload; repair reports and patch diffs under `reports/` and `working/`.

3. **Does the system support reopening completed sessions?**  
   **Yes.** Read-only wizard + artifact downloads + audit page.

4. **Does the system support reopening failed sessions?**  
   **Yes.** Failure card, mitigation metadata, preserved evidence, audit/archive.

5. **Does the system support reopening clarification-needed sessions?**  
   **Yes** when `paused_user` with unanswered `question_asked`. **No** honest “resume” for terminal `author_contract_clarification_required` without answering — treated as failure path.

6. **Does the system support true step-level resume after a failed internal stage?**  
   **No.** Failed terminal states require a new `/run` that re-invokes the orchestrator. Idempotency cache helps repeated tool calls within a run, not cross-run stage continuation.

7. **Does “Retry with same inputs” rerun the full flow or resume from failed stage?**  
   **Full flow re-execution** with prior event log available to the orchestrator/model. Documented in `docs/FAULT_TOLERANCE_MODEL.md`.

8. **Does the UI wording clearly distinguish resume vs retry?**  
   **Partially.** Mitigation copy says “Retry with same inputs” but `ResumeBanner` name/copy implies continuation; dashboard says “save and return later” without clarifying pre-run prompt loss. API always sets `can_resume: false`.

9. **Are artifacts downloadable after returning?**  
   **Yes** — `artifactUrl`, `archiveUrl`, output preview cards, audit JSON export (`test_archive.py`).

10. **Does archive-on-demand work after returning?**  
    **Yes** — `GET /sessions/{id}/archive.zip` builds if missing (`persistence/archive.py`, `files.py`).

11. **Does the event log remain the canonical source of truth?**  
    **Yes** (INV-6). Mitigation is computed at read time from events, not a separate mutable store.

12. **What happens if the user refreshes during a running session?**  
    Client remounts; `useSessionState` refetches session + full event tail; polling resumes at 2s. Background task keeps running server-side.

13. **What happens if backend process restarts during a running session?**  
    Startup calls `recover_all_orphaned_running_sessions` → `failed_other` + `workflow_interrupted` + `workflow_failed` event. User must POST `/run` to retry (full rerun). **Not** mid-loop resume.

14. **Which state is durable on disk?**  
    SQLite session row, `events.jsonl`, `manifest.json`, workspace files, archives, uploaded/generated artifacts.

15. **Which state is only frontend/local/transient?**  
    Author/Repair input-stage description textarea, template picker before `/run`, `runPending` flag, expense review decisions (`sessionStorage`), TanStack Query cache, incremental event cursor in hook ref.

16. **Are review decisions local-only or persisted?**  
    **Local-only** (`sessionStorage` key `agentforge:expense-review:{sessionId}`). Lost on new browser/profile; not in event log.

17. **Is the current behaviour enough for the take-home requirement?**  
    **Yes, with honest documentation** — users can leave and return to active, paused, completed, and failed sessions; artifacts and audit survive. The prototype does **not** need to claim arbitrary step-level resume.

18. **What would be the minimal improvement if not?**  
    See Phase 5 (docs + targeted UX, no lifecycle rewrite).

---

## Phase 4 — Classification table

| Area | Status | Evidence | Risk | Suggested fix |
|---|---|---|---|---|
| Completed Author resume | **GREEN** | CompletedStage, output cards, archive/artifact URLs | Low | — |
| Failed Author resume | **GREEN** | `FailureCard`, `failure_mitigation`, workspace artifacts | Low | Wire `retry_same_inputs` button to POST `/run` |
| Running Author resume | **AMBER** | Background task + polling works; API restart → interrupted | Medium | Document restart behaviour; optional “Continue run” after interrupt |
| Clarification resume | **GREEN** | `question_asked` + `UserQuestionPanel` + `/answer` + auto `/run` | Low | — |
| Completed Repair resume | **GREEN** | Repair cards + downloads | Low | — |
| Failed Repair resume | **GREEN** | Failure + repair-specific copy | Low | Same retry button wiring |
| Artifact persistence | **GREEN** | Workspace + lazy archive; `test_archive.py` | Low | — |
| Archive-on-demand | **GREEN** | `GET .../archive.zip` | Low | — |
| Retry wording | **AMBER** | Correct in `failure-mitigation.ts`; `ResumeBanner` name misleading; `can_resume` always false | Medium | Rename banner copy; document retry vs resume |
| Frontend local-only review decisions | **AMBER** | `sessionStorage` only in `expense-review-queue.tsx` | Medium | Label “Review choices saved in this browser tab only” |
| Backend restart resilience | **AMBER** | Orphan recovery → terminal fail, not resume | **High** | Document in SESSION_STATE_AND_RESUME.md; optional future: pause-not-fail on restart |
| Paused approval resume | **AMBER** | `/approve` does not call `/run` (unlike clarification UI) | Medium | After approve, POST `/run` or show explicit “Continue” button |
| Pre-run prompt persistence | **AMBER** | Description in React state only | Medium | Persist draft in event on blur or warn on leave |

**Legend:** GREEN = fully satisfies honest resume expectation · AMBER = works with documented limits · RED = user cannot meaningfully return

---

## Phase 5 — Minimal recommendations (no implementation)

### 1. Add `docs/SESSION_STATE_AND_RESUME.md`

Suggested wording:

> Sessions persist uploaded files, generated artifacts, event logs, validation results, reports, and archives on disk and in SQLite. Users can leave and return to **completed**, **failed**, **paused** (clarification), and **in-progress** sessions by reopening the same URL or using the dashboard.
>
> **Pause resume:** Answer a clarification or grant an approval, then start the next run (`POST /run`). Clarification UI does this automatically; approval may require an explicit continue action.
>
> **Retry:** After a terminal failure, “Retry with same inputs” starts a **new execution** of the workflow using the same session, uploads, and event history — not continuation from an arbitrary internal step.
>
> **Not persisted:** Workflow description text entered before the first run (until Start is clicked). Expense review row decisions are stored in the browser tab only.
>
> **API restart:** If the backend restarts during a run, orphaned sessions are marked failed with `workflow_interrupted`; use Retry or start a new session.

### 2. Smallest UX fixes (when implementing)

- Wire `retry_same_inputs` in `FailureMitigationActions` to POST `/run` (empty body).
- After `ApprovalPanel` grant, mirror `UserQuestionPanel` — call `/run` or show “Continue workflow”.
- Rename or reword `ResumeBanner` → “Session progress” / “Next step” (avoid “Resume” where behaviour is retry).
- Dashboard: qualify “save and return later” — “Uploads and results are saved; finish describing your workflow before leaving the input screen.”
- Expense review queue: one-line note that review choices are browser-local.

### 3. Tests to add later (not in this audit)

- Reopen `created` + upload → files visible, description empty.
- Reopen `paused_user` after reload → question visible.
- Reopen `failed_*` → mitigation + archive link.
- GET session after soft-delete → 404 on wizard.

---

## References (primary)

| Artifact | Path |
|---|---|
| Session schemas | `apps/api/src/agentforge/schemas/session.py`, `common.py` |
| Session router | `apps/api/src/agentforge/api/routers/sessions.py` |
| Orphan recovery | `apps/api/src/agentforge/orchestrator/runner.py`, `api/lifespan.py` |
| Fault tolerance doc | `docs/FAULT_TOLERANCE_MODEL.md` |
| Resume tests | `apps/api/tests/test_resume_after_restart.py`, `test_sessions_lifecycle.py`, `test_runner_orphan_recovery.py` |
| Author wizard | `apps/web/app/author/[sid]/page.tsx` |
| Repair wizard | `apps/web/app/repair/[sid]/page.tsx` |
| Polling hook | `apps/web/src/lib/use-session-state.ts` |
| Sample failed workspace | `.workspaces-deepseek-rerun/ce21e7e5-ce6f-4332-a645-d17075a4866d/` |

---

## Report back (submission checklist)

| Item | Answer |
|---|---|
| Audit path | `reports/resume_behavior_audit.md` |
| Overall status | **AMBER** |
| Biggest gap | No true step-level resume; API restart fails in-flight runs as `workflow_interrupted` |
| Recommended minimal fix | Add `docs/SESSION_STATE_AND_RESUME.md` + wire retry/approval continue + honest banner copy |
| Submission-ready | **Yes**, if resume/retry semantics are documented honestly and not oversold as step-level continuation |
| Verification pass (2026-05-25) | **Updated** — Phase 3 gained scenario table + question index; Phases 1–2, 4–5 verified against codebase |
