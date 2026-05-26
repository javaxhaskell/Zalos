# Fault Tolerance Model

AgentForge surfaces failures in **two layers** so finance users can act while engineers retain full audit evidence.

## Two layers

| Layer | Audience | Content |
|---|---|---|
| Finance-user | Operators, operators | Title, summary, what we found, mitigation actions, primary CTA |
| Technical audit | Engineering | Append-only `events.jsonl`, validation reports, stack traces, generated files, archive |

Failures **never** mark a session `completed`. Validation gates are **not** weakened. Raw logs are **never** removed.

## Failure taxonomy

| Category | Example codes | Typical cause |
|---|---|---|
| `upload` | `malformed_csv`, `file_too_large` | Bad or oversized upload |
| `schema` | `author_intent_schema_mismatch`, `missing_required_columns` | File/workflow mismatch |
| `clarification` | `author_contract_clarification_required` | Ambiguous dates/columns (paused, not terminal) |
| `model_contract` | `author_contract_planning_failed` | LLM plan failed contract validation |
| `codegen` | `author_code_generation_failed`, `safety_validation_failed` | Generated code or safety gate |
| `pytest` | `generated_pytest_failed` | Model-authored tests failed |
| `validation` | `universal_validation_failed`, `contract_specific_validation_failed` | Deterministic tier checks |
| `golden` | `golden_output_comparison_failed` | Optional expected-output mismatch |
| `sandbox` | `sandbox_crash`, `command_timeout` | Runtime / timeout |
| `budget` | `budget_exhausted_*` | Token/step/wall/file limits |
| `repair` | `repair_cannot_reproduce`, post-fix `test_failed` | Evidence or patch validation |
| `model` | `failed_model` session status | Provider error |

## User mitigation actions

Structured actions are returned on `GET /sessions/{id}` as `failure_mitigation` for terminal failed sessions:

| Action | Meaning |
|---|---|
| `start_new_session` | Create a fresh session |
| `edit_workflow_description` | Revise the workflow text and re-run |
| `upload_replacement_file` | Upload a corrected sample file |
| `answer_clarification` | Respond to a paused clarification question |
| `retry_same_inputs` | Call `/run` again on the same session (re-executes flow) |
| `download_audit_package` | Download `archive.zip` |
| `open_technical_details` | Expand event log / technical accordion |
| `open_repair` | Start Repair with generated/working agent package |
| `provide_expected_output` | Supply `expected_output.csv` / golden file |
| `simplify_workflow` | Shorten description or use smaller sample |
| `increase_budget` | Raise limits (settings / ops) |
| `upload_problem_report` | Repair: upload agent ZIP + clearer problem report |

Safety failures **never** suggest bypassing checks.

## Raw audit preservation

- Every failure emits `workflow_failed` with `error_code`, `message`, and optional `technical_detail`, `failed_check`, `failed_layer`, `validation_failures`, `pytest_summary`.
- Custom Author build failures also persist manifest `completion.failure` and attempt `archive.zip` generation.
- `GET /sessions/{id}/events` and `GET /audit/export/{id}` remain unchanged.

## Retry / resume behaviour

| State | Behaviour |
|---|---|
| `paused_user` | Answer via `/answer`, then `/run` |
| `paused_approval` | Approve/decline, then `/run` |
| Terminal `failed_*` | **Cannot resume in place**; `/run` starts a new execution with prior events in model context |
| `retry_safe: true` | Mitigation flag — retry is unlikely to corrupt state |

## API shape

```json
{
  "failure_mitigation": {
    "user_title": "The generated agent failed its tests",
    "user_summary": "...",
    "what_we_found": "3 failed, 0 passed",
    "evidence_items": ["generated/tests/test_agent.py", "archive.zip"],
    "mitigation_actions": ["edit_workflow_description", "download_audit_package"],
    "primary_action": "edit_workflow_description",
    "secondary_actions": ["download_audit_package"],
    "retry_safe": true,
    "can_resume": false,
    "can_download_archive": true,
    "technical_details_ref": "validation_report",
    "failure_category": "pytest"
  }
}
```

## Examples

### Author — generated pytest failure

1. User sees: **The generated agent failed its tests**
2. Evidence: pytest summary, validation report, archive
3. Actions: clarify business rules, provide expected output, download audit, optionally open Repair

### Repair — cannot reproduce

1. User sees: **Could not reproduce the reported issue**
2. Evidence: pre-fix pytest summary, discovered issue if any
3. Actions: upload clearer problem report + agent ZIP, download audit

## Implementation

- Mapping: `apps/api/src/agentforge/fault_tolerance/user_mitigation.py`
- Schema: `FailureMitigation` on `Session` (computed at read time)
- Frontend: `FailureCard` prefers API mitigation; `ux-language.ts` remains fallback
- Audit: `reports/fault_tolerance_user_mitigation_audit.md`
