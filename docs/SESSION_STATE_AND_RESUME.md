# Session State and Resume

AgentForge sessions are **durable and reopenable**. What you can return to, what is saved on the server, and what “retry” means are spelled out here so UI copy and operator expectations stay honest.

**Thesis (INV-6):** The append-only event log (`events.jsonl`) is the source of truth. The backend reconstructs workflow state from SQLite, events, and workspace files — not from browser-only state.

---

## What persists — Author workflow

| Artifact | Location | When it exists |
|---|---|---|
| Uploaded sample files | `uploads/` + `file_uploaded` events | After upload |
| Workflow description (post-run) | `DECISION_INPUT` event (`author_user_workflow`) | After first successful `POST /run` |
| Clarification Q&A | `question_asked` / `answer_received` events | After agent asks; answer persisted on submit |
| Event log | `events.jsonl` | From session creation |
| Contract / code / tests | `generated/` | After codegen stages |
| Outputs | `outputs/` | After agent run |
| Validation reports | `reports/` | After validation |
| Manifest | `manifest.json` | Updated through lifecycle; finalized at terminal states |
| Audit archive | `archive.zip` | On demand or at terminal packaging |

**Browser-local draft (pre-run):** If you type a workflow description before clicking Start, the wizard may restore it from `sessionStorage` in the same browser tab. That draft is **not** server-backed until `/run` sends `user_message`.

---

## What persists — Repair workflow

| Artifact | Location | When it exists |
|---|---|---|
| Agent ZIP / files | `working/` + upload or `fixture_loaded` events | After upload or sample load |
| Problem report (post-run) | First `user_message` on `POST /run` (recorded in events) | After first `/run` |
| Before / after logs | `outputs/_logs/` + tool events | During and after run |
| Patch | `working/` diffs + patch events | After repair apply stage |
| Repair report | `reports/` | After validation |
| Manifest | `manifest.json` | Same as Author |
| Audit archive | `archive.zip` | Same as Author |

**Browser-local draft (pre-run):** Problem-report text typed before Start may be restored from `sessionStorage` in the same tab only.

---

## Leave and return

| Session state | Reopenable? | What you see |
|---|---|---|
| `created` | Yes | Input stage; uploads restored from server; description draft from tab storage if available |
| `running` | Yes | Live progress via polling; background task continues server-side |
| `paused_user` | Yes | Clarification panel; answer then continue |
| `paused_approval` | Yes | Approval panel; approve/decline then continue |
| `completed` | Yes | Read-only outputs, validation, downloads |
| `failed_*` | Yes | Failure card, evidence, mitigation actions, audit |
| Soft-deleted | No (404) | Restore from dashboard Recently deleted tab first |

Open any saved session from the dashboard list or direct URL: `/author/{id}` or `/repair/{id}`.

---

## Retry with same inputs (full rerun)

**“Retry with same inputs”** means `POST /sessions/{id}/run` on the **same session** using persisted uploads, prior events, and (when recorded) the original workflow description.

It is a **full orchestrator rerun**, not continuation from an arbitrary internal model or tool stage. Prior events remain in the log and may inform the model, but the flow starts a new execution pass.

Use this after terminal failures when mitigation lists `retry_same_inputs` and `retry_safe: true`.

**Do not call this “resume.”** Resume language is reserved for reopening a saved session or continuing after a **pause gate** (clarification or approval).

---

## Pause gates (continue after answer or approval)

These are **not** step-level resume either; they are explicit human gates:

1. **Clarification (`paused_user`):** `POST /answer`, then `POST /run`. The clarification UI triggers `/run` automatically after a successful answer.
2. **Approval (`paused_approval`):** `POST /approve` or `/reject`, then `POST /run` to continue.

The loop reads the new events on the next `/run`.

---

## NOT supported

- **Arbitrary resume from a failed model or tool stage** mid-pipeline without a full rerun.
- **Step-level continuation** after backend crash, sandbox failure, or codegen failure as if nothing happened.
- **Re-running a completed session** — `POST /run` returns 409 on `completed`.
- **Server persistence of expense review row decisions** — see below.

`failure_mitigation.can_resume` is always `false` because true in-place stage resume is not implemented.

---

## Backend restart

If the API process restarts while a session is `running`, startup orphan recovery marks it `failed_other` with `workflow_interrupted`.

**Requires retry:** POST `/run` again (full rerun) or start a new session. There is no mid-loop reconstruction of the in-flight background task.

---

## Expense review decisions (frontend-only)

Row-level approve/flag decisions in the expense review queue are stored in **`sessionStorage` only** (tab-scoped). They are **not** written to the event log or SQLite.

Export reviewed output via the download menu to keep decisions; clearing site data or switching browsers loses them.

---

## Event log as source of truth

All durable workflow facts — uploads, decisions, tool calls, failures, approvals, artifacts — must appear in `events.jsonl` (and mirrored session row fields derived from it).

UI state, `sessionStorage` drafts, and in-memory wizard fields are **convenience only** unless exported or submitted through an API endpoint that appends an event.

**Audit endpoints:**

- `GET /sessions/{id}/events` — live tail for the wizard
- `GET /audit/export/{id}` — full chain + manifest for engineers

---

## Related docs

- [FAULT_TOLERANCE_MODEL.md](./FAULT_TOLERANCE_MODEL.md) — failure taxonomy and mitigation actions
- [ARCHITECTURE.md](../ARCHITECTURE.md) — persistence layout
- `reports/resume_behavior_audit.md` — detailed scenario trace
