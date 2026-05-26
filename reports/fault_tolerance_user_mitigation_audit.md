# Fault Tolerance User Mitigation Audit

**Date:** 2026-05-25  
**Scope:** AgentForge Author + Repair failure UX (backend + frontend)  
**Thesis (INV-6):** Every state change must be visible; failures are never hidden or faked.

---

## Executive summary

AgentForge already has a **two-layer failure model**: typed `terminal_error_code` on the session row, `workflow_failed` events with structured payloads, and frontend cards (`FailureCard`, `AuthorCustomBuildFailureCard`) with plain-English copy in `ux-language.ts`. Gaps: **many layer-specific error codes collapse to `failed_other`**, mitigation actions are **text-only** (no structured API actions), **`generated_pytest_failed` and tier-specific validation codes lack dedicated frontend copy**, archive download is **inconsistent** on generic failures, and **`Session` GET does not expose mitigation metadata**.

---

## Backend inventory

| Area | Location | Notes |
|---|---|---|
| Session status enum | `schemas/common.py` | `failed_budget`, `failed_model`, `failed_sandbox`, `failed_user_reject`, `failed_other` |
| Error codes | `schemas/common.py` `ErrorCode` | 40+ codes including upload, validation tiers, budget, repair |
| Session row | `schemas/session.py`, `persistence/session_store.py` | `terminal_error_code`, `budget`, no mitigation field |
| `workflow_failed` emission | `author_custom_build._fail`, `author_flow`, `author_llm_authoring`, `repair_flow`, `agent/loop.py`, `runner.py` | Payload: `error_code`, `message`, `stage`, optional `technical_detail`, `failed_check`, `failed_layer`, `validation_failures`, `pytest_*`, `discovered_issue_summary` |
| Validation tier → error code | `validation/validation_architecture.py` | Maps universal / contract / pytest / golden / safety / artifact |
| Archive on failure | `author_custom_build.py` L2507–2520 | Builds `archive.zip` + `ARTIFACT_GENERATED` before `_fail` for custom build failures |
| Archive lazy build | `api/routers/files.py` GET `/archive.zip` | Builds if missing |
| SESSION_README | `persistence/archive.py` | Written during archive build; includes status, validation, reproduction steps |
| Resume | `POST /sessions/{id}/run` | `FAILED_*` may restart; `PAUSED_USER` uses `/answer`; no true resume of terminal failures |
| Upload errors | `api/routers/files.py`, `api/errors.py` | HTTP 400 with `malformed_csv`, `unsupported_file_type`, etc. |
| Clarification | `author_flow.py`, `author_date_clarification.py` | `question_asked` → `paused_user`; not a terminal failure |

---

## Frontend inventory

| Component | Path | Role |
|---|---|---|
| `FailureCard` | `components/failure-card.tsx` | Generic failed session; uses `humaniseTerminalFailure` |
| `AuthorCustomBuildFailureCard` | `components/author-custom-build-failure-card.tsx` | `author_validation_failed` / `author_custom_build_failed`; archive + artifact links |
| `ErrorBanner` | `components/error-banner.tsx` | HTTP/upload errors; partial code map |
| `ResumeBanner` | `components/resume-banner.tsx` | Next-step hints; generic on most failures |
| `ux-language.ts` | `lib/ux-language.ts` | `TERMINAL_ERROR_COPY` (~25 codes); missing tier-specific validation codes |
| Author/Repair pages | `app/author/[sid]/page.tsx`, `app/repair/[sid]/page.tsx` | Route to specialized vs generic failure cards |
| Technical accordion | `TechnicalDetailsDisclosure`, event log | Preserved on failed pages |

**Retry UI:** Only "Start a new session" and "Open full audit" links — no structured action buttons from API.

---

## Failure class table

| Failure code / signal | Workflow | Where raised | Current user message | Current technical evidence | Current available actions | Problem | Recommended mitigation |
|---|---|---|---|---|---|---|---|
| `malformed_csv` | Author (upload) | File upload validation | ErrorBanner: "We couldn't read that file" | HTTP `technical_detail` | Re-upload | Blocks before session run; no session card | Upload replacement CSV; fix delimiter/encoding |
| `unsupported_file_type` / `file_too_large` | Both | `files.py` upload | ErrorBanner titles | HTTP detail | Re-upload | Same | Upload CSV/XLSX/ZIP within limits |
| `missing_required_columns` / `ambiguous_schema` | Author | CSV inspect / intent gate | Intent mismatch card OR generic | `workflow_failed` + decision_input | Start new session | Good for intent; ambiguous dates use clarification | Answer clarification or fix columns |
| `author_contract_clarification_required` | Author | Contract planning | Paused (not failed) | `question_asked` event | Answer question | Correct — not terminal | Answer clarification panel |
| `author_contract_planning_failed` | Author | `author_llm_authoring.py` | TERMINAL_ERROR_COPY headline | `technical_detail`, contract validation errors | Audit link, new session | Good copy; no archive button on generic card | Clarify workflow; provide expected_output.csv; download audit |
| `author_contract_review_failed` | Author | `author_llm_authoring.py` | TERMINAL_ERROR_COPY | Same | Same | Same | Same |
| `author_code_generation_failed` / `author_test_generation_failed` | Author | LLM authoring stages | TERMINAL_ERROR_COPY | Model stage + logs | New session | Generic next step | Clearer description; smaller sample |
| `author_generated_code_failed` / `generated_code_failed` | Author | Execution / runtime | "Agent ran into an issue" | Traceback in `technical_detail` | Audit, new session | Technical detail sometimes truncated | Download audit; open Repair if package exists |
| `generated_pytest_failed` | Author | `validation_architecture`, `author_custom_build` | Falls through to `author_validation_failed` card OR generic `failed_other` | `pytest_summary`, `failed_check` | Custom build card has artifacts | **No dedicated TERMINAL_ERROR_COPY entry** | Clarify business rules; expected_output.csv; Repair path |
| `universal_validation_failed` | Author | Tier classifier | Generic / custom build card | `failed_layer`, validation report | Partial artifacts | Layer not surfaced to user | Name universal tier in "What we found" |
| `contract_specific_validation_failed` | Author | Tier classifier | Generic | Contract check names | Same | Same | Clarify workflow rules |
| `golden_output_comparison_failed` | Author | `layer_golden_output` | Generic | Golden diff in report | Same | User may not know about golden file | Provide expected_output.csv |
| `safety_validation_failed` | Author | Safety layer | Generic | Safety check detail | Same | Must not suggest bypass | Fix unsafe patterns in description/sample |
| `artifact_validation_failed` | Author | Artifact gate | Generic | Missing artifact paths | Same | Same | Review generated files in audit |
| `author_validation_failed` | Author | `author_custom_build._fail` | AuthorCustomBuildFailureCard | Full tier evidence + diagnostics | **Download archive**, validation report | Best failure UX today | Extend pattern to all validation failures |
| `author_custom_build_failed` | Author | Custom pipeline | AuthorCustomBuildFailureCard | Same | Same | Good | Same |
| `author_intent_schema_mismatch` | Author | `author_flow.py` | Dedicated card | `missing_columns`, explanation | New session | Good | Upload matching file or change workflow |
| `author_custom_workflow_not_validated` | Author | `author_flow.py` | Dedicated card | Detected columns | New session | Good | Use validated template |
| `repair_cannot_reproduce` | Repair | `repair_flow.py` | RepairCannotReproduceResult | `discovered_issue_summary` | Audit, new session | Good specialized UX | Upload repo with failing tests + problem report |
| `test_failed` / post-fix pytest fail | Repair | `repair_flow.py` | Generic FailureCard | Pytest in events | Audit | "Validation failed after patch" in README only | Download audit; revise problem report |
| `budget_exhausted_*` | Both | `agent/loop.py` | TERMINAL_ERROR_COPY + BudgetBanner | Budget counters | Extend/finalise if paused | Good when budget_warned fired | Smaller file / simpler workflow / extend budget |
| `sandbox_crash` / `failed_sandbox` | Both | Sandbox runner, loop | Generic / "execution error" | stderr, exit code | New session | **Weak dedicated copy** | Download audit; retry with smaller input |
| `failed_model` | Both | Model client errors | SESSION_STATUS_FAILURE_COPY | Model error in events | New session | Generic | Retry later; check API key |
| `workflow_interrupted` | Both | Orphan recovery `runner.py` | TERMINAL_ERROR_COPY | Interrupt message | New session | OK | Start new session |
| `user_abandoned` | Both | Cancel endpoint | Generic stopped | Cancel event | New session | OK | Intentional |
| `unknown` / unhandled exception | Both | `runner.py` catch-all | `failed_other`: "Session stopped" | Raw exception | Audit | **Worst UX** — hides specific code | Map to specific code where possible |
| `approval_declined` | Both | Approval flow | `failed_user_reject` | Decline reason | New session | OK | Re-run and approve |

---

## Brief failure classes — coverage

| Brief class | Covered? | Gap |
|---|---|---|
| Sandbox crash | Partial | Code exists; weak user copy; no audit CTA on generic card |
| Model failure | Partial | `failed_model` status copy only |
| Malformed file | Yes (upload) | HTTP-only; pre-session |
| Failing tests | Partial | Custom build card good; generic pytest codes weak |
| Bad code edits | Partial | Repair diff visible on success; post-fix fail is generic |

---

## Audit questions (15)

1. **Which failure codes already exist?** — Full `ErrorCode` enum in `schemas/common.py` (~40 codes); session statuses `failed_*` (6 terminal failure statuses).

2. **Which are too generic?** — `failed_other`, `unknown`, `author_validation_failed` (aggregates many tiers), `author_generated_code_failed`.

3. **Which messages are finance-user-readable?** — Intent mismatch, custom workflow not validated, repair cannot reproduce, author custom build failure card, budget copies, contract planning/review copies.

4. **Which only tell the user to retry?** — `sandbox_crash`, `workflow_interrupted`, `failed_model`, generic `failed_other` next step.

5. **Which preserve downloadable evidence?** — Custom build failures (output CSV, validation report, archive); partial output paths on some contract failures.

6. **Which produce an archive?** — `author_custom_build` failure path explicitly; lazy GET `/archive.zip` otherwise if workspace exists.

7. **Which can resume?** — `paused_user`, `paused_approval`, `running` orphan recovery; **not** terminal `failed_*`.

8. **Which require new session?** — All terminal failures; `/run` on failed session starts fresh flow with prior event context.

9. **Suggest editing workflow prompt?** — Contract planning/review, code/test generation, validation tier failures, pytest failures.

10. **Suggest uploading better file?** — Intent mismatch, malformed upload, missing columns, custom build ingest failures.

11. **Suggest expected_output.csv?** — Golden comparison failed, contract planning when golden hinted, pytest/validation failures with partial output.

12. **Suggest opening Repair?** — Generated package exists (agent.py + tests) after author pytest/code failure — **not surfaced in UI today**.

13. **Expose enough technical evidence?** — Custom build card + audit page yes; generic FailureCard depends on `technical_detail` quality (sometimes artifact tree only).

14. **Not covered at all?** — Structured API mitigation actions; tier-specific titles for `generated_pytest_failed`, `universal_*`, `contract_specific_*`, `golden_*`, `safety_*`; Repair post-patch failure card; Open Repair action.

15. **UI components needing changes?** — `FailureCard` (actions + archive), `ux-language.ts` (missing codes), `Session` API response, optional shared mitigation renderer.

---

## Phase 2 — Minimal design

### Approach

Extend **`Session`** GET response with optional computed **`failure_mitigation`** (no DB migration). Reuse `workflow_failed` payload + manifest `completion.failure` for evidence. Frontend prefers API mitigation; falls back to existing `ux-language.ts`.

### Schema (additive)

```python
class MitigationAction(StrEnum):
    START_NEW_SESSION = "start_new_session"
    EDIT_WORKFLOW_DESCRIPTION = "edit_workflow_description"
    UPLOAD_REPLACEMENT_FILE = "upload_replacement_file"
    ANSWER_CLARIFICATION = "answer_clarification"
    RETRY_SAME_INPUTS = "retry_same_inputs"
    DOWNLOAD_AUDIT_PACKAGE = "download_audit_package"
    OPEN_TECHNICAL_DETAILS = "open_technical_details"
    OPEN_REPAIR = "open_repair"
    PROVIDE_EXPECTED_OUTPUT = "provide_expected_output"
    SIMPLIFY_WORKFLOW = "simplify_workflow"
    INCREASE_BUDGET = "increase_budget"
    UPLOAD_PROBLEM_REPORT = "upload_problem_report"

class FailureMitigation(StrictModel):
    user_title: str
    user_summary: str
    what_we_found: str | None = None
    evidence_items: list[str] = []
    mitigation_actions: list[MitigationAction] = []
    primary_action: MitigationAction
    secondary_actions: list[MitigationAction] = []
    retry_safe: bool = False
    can_resume: bool = False
    can_download_archive: bool = False
    technical_details_ref: str = "events"  # events | manifest | validation_report
    failure_category: str  # upload | schema | clarification | model | codegen | pytest | validation | golden | sandbox | budget | repair
```

### Twelve required classes — mapping

| Class | user_title (example) | primary_action | retry_safe | can_download_archive |
|---|---|---|---|---|
| 1 Malformed file | We couldn't read that file | upload_replacement_file | true | false |
| 2 Missing columns / ambiguity | Your file doesn't match this workflow | upload_replacement_file | true | false |
| 3 Clarification needed | Quick question before we continue | answer_clarification | true | false |
| 4 Model contract failure | We couldn't finalize the workflow plan | edit_workflow_description | true | true |
| 5 Codegen / safety failure | The generated agent didn't pass safety checks | edit_workflow_description | true | true |
| 6 Generated pytest failure | The generated agent failed its tests | provide_expected_output | true | true |
| 7 Deterministic validation failure | Output didn't pass validation | download_audit_package | true | true |
| 8 Golden-output mismatch | Output didn't match expected results | provide_expected_output | true | true |
| 9 Sandbox/runtime crash | An unexpected execution error occurred | download_audit_package | true | true |
| 10 Budget/limit failure | Session budget reached | simplify_workflow | true | true |
| 11 Repair cannot reproduce | Could not reproduce the reported issue | upload_problem_report | true | true |
| 12 Repair failed after patch | Checks still failing after the fix | download_audit_package | false | true |

Safety failures **never** include actions that imply bypassing checks.

---

## Implementation scope (Phase 3)

1. `agentforge/fault_tolerance/user_mitigation.py` — mapping engine  
2. `Session.failure_mitigation` optional field + GET enrichment  
3. `FailureCard` — render API actions + conditional archive button  
4. `ux-language.ts` — add missing TERMINAL_ERROR_COPY entries as fallback  
5. Docs + 10 backend tests  

---

## Limitations (post-implementation)

- Mitigation is **computed at read time**, not persisted on the event log.  
- `RETRY_SAME_INPUTS` maps to existing `/run` on failed session — works but re-executes full flow.  
- True pause/resume of terminal failures is **out of scope**.  
- OpenAPI/TS mirror must be regenerated for typed frontend fields.
