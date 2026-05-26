# ADR-0006 — Approval model

**Status:** accepted
**Date:** 2026-05-21

## Context

The assignment requires that finance users see questions and approvals as structured artefacts, and that approvals visibly gate state-changing actions. The thesis (INV-3) demands that approvals be hard state-machine transitions, not advisory popups.

## Decision

Approvals are **persisted `ApprovalRequest` rows + state-machine transitions**. The agent loop literally cannot dispatch a `requires_approval=True` tool without a recorded `APPROVAL_GRANTED` event for the corresponding `(session_id, step)`.

**Approval gates fire at:**

1. Author flow — Confirm Requirements (after Q&A, before code generation).
2. Author flow — Review Diff (after every batch of `write_file` / `apply_patch`, before they actually land in `generated/`).
3. Author flow + Repair flow — first-time `run_python_script` and `run_pytest` per session (one consent covers subsequent same-session executions).
4. Repair flow — Confirm Summary (after triage, before reproduce).
5. Repair flow — Review Patch (after `propose_patch`, before `apply_patch`).
6. Author + Repair — Finalise (before `archive_workspace` + `finalise_session`).

**Approval UX:** the UI surfaces a **business-level summary** primary; the technical diff is one click away. Approve is a single button; Decline requires a free-text reason. CRITICAL exceptions surface an SOP-acknowledgment checkbox above the action.

**Auto-approval rules (whitelist):** `generate_validation_report`, `generate_repair_report`. Each cites this ADR in its `ToolDefinition.adr_override`.

**Decline behaviour:**

- Mid-loop decline (e.g., diff review): loop back to prior phase with the decline reason recorded as a `DECISION_INPUT` event. Bounded to 2 iterations; after that, terminal `failed_user_reject`.
- Run-consent decline (refusing to execute model-generated code at all): terminal `failed_user_reject`.
- Finalise decline: loop back to review state (rare).

## Options considered

| Option | Verdict |
|---|---|
| **State-machine gate with persisted `ApprovalRequest`** | Selected |
| Advisory popup (UI confirms; backend trusts) | Bypassable; rejected |
| Always-on (no approval) | Disqualifying for finance UX; rejected |
| Approval as a UI affordance only (no backend gate) | Violates INV-3; rejected |

## Rationale

- The persistence of `ApprovalRequest` + the executor's check for `APPROVAL_GRANTED` mean removing the UI does not weaken the gate; an automated client would still need to grant approval via the API.
- Business-level summary primary, technical diff secondary, fits finance-user comprehension. Finance users can read code but don't have to.
- Auto-approval whitelist is small, declared in tool definitions, and ADR-cited; no implicit auto-approvals exist.
- Bounded decline cycles (2 iterations) prevent infinite Q&A or infinite patch revision.

## Consequences

- The agent loop has an explicit `paused_approval` state and a resume path that re-enters with the approval recorded.
- The frontend's `ApprovalPanel` is one of the key components; it must render business summary, diff (collapsed), Approve / Decline (with required reason on Decline).
- Approvals are auditable: every `APPROVAL_REQUESTED` / `APPROVAL_GRANTED` / `APPROVAL_DECLINED` event in `events.jsonl` includes actor, reason (if any), and timestamp.
- E2E tests assert that bypassing the approval endpoint cannot advance state.

## Reversal condition

Soften gates only if production evals demonstrate zero unauthorized-execution risk, AND there is product-team approval for a less restrictive flow. For the prototype: no relaxation.
