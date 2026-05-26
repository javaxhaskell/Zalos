# AgentForge — Contracts

> Status: final submission snapshot. Owned by `agentforge-architect`. Updated only via new ADRs.

All implementation skills import from these contracts. No skill invents a type. New types are filed as issues to `agentforge-architect`, who produces an ADR and updates this document.

## 1. Pydantic schemas

The canonical Python types live under `apps/api/src/agentforge/schemas/`. Each module is a single domain.

### `common.py` — enums + base

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Workflow(StrEnum):  AUTHOR, REPAIR
class SessionStatus(StrEnum): CREATED, RUNNING, PAUSED_USER, PAUSED_APPROVAL,
    COMPLETED, FAILED_BUDGET, FAILED_MODEL, FAILED_SANDBOX, FAILED_USER_REJECT,
    FAILED_OTHER, AUTO_ARCHIVED
class AuthorPhase(StrEnum):  AUTHOR_TEMPLATE, AUTHOR_UPLOAD, AUTHOR_PROFILE,
    AUTHOR_DESCRIBE, AUTHOR_INFER, AUTHOR_QA, AUTHOR_CONFIRM, AUTHOR_GENERATE,
    AUTHOR_REVIEW_DIFF, AUTHOR_APPLIED, AUTHOR_RUN, AUTHOR_VALIDATE,
    AUTHOR_REVIEW, AUTHOR_FINALISE
class RepairPhase(StrEnum):  REPAIR_UPLOAD, REPAIR_LOADED, REPAIR_PROBLEM,
    REPAIR_TRIAGE, REPAIR_CONFIRM_SUMMARY, REPAIR_REPRODUCE, REPAIR_NEED_INFO,
    REPAIR_DIAGNOSE, REPAIR_PROPOSE, REPAIR_REVIEW_PATCH, REPAIR_APPLY,
    REPAIR_VALIDATE, REPAIR_REPORT, REPAIR_FINALISE
class RiskLevel(StrEnum):    READ, LOW_WRITE, HIGH_WRITE
class ActorType(StrEnum):    USER, SYSTEM, MODEL
class ApprovalStatus(StrEnum): PENDING, GRANTED, DECLINED
class ErrorCode(StrEnum): (see ARCHITECTURE.md §8 for the full list)
```

### `session.py`

- `BudgetStatus` — counters + limits for tokens, tool calls, steps, wall, files.
- `SessionCreate` — request: `{ workflow }`.
- `Session` — full session row.
- `SessionListItem` / `SessionList` — listing response.
- `ResumeManifest` — `manifest.json` shape on disk.

### `event.py`

- `WorkspaceEvent` — chronological event written to `events.jsonl`.
- `EventKind` (StrEnum) — see §3 below.
- `EventEnvelope` — `{ id, session_id, ts, step, kind, payload, prev_event_id }`.
- Typed payload classes per event kind (discriminated union).

### `tool.py`

- `ToolDefinition` — registry entry: name, description, input/output schema names, risk_level, requires_approval, idempotent, phases, authorize-callable-name, adr_override.
- `ToolInvocation` — per-call record: id, session_id, step, tool_name, args_hash, idempotency_key, attempt, started_at, ended_at, success.
- `ToolObservation` — result: invocation_id, success, output_summary, output_path, error_code, error_message, latency_ms.
- `ApprovalRequest` — pending approval: id, session_id, step, kind, payload_preview, diff_paths, created_at.
- `ApprovalDecision` — user response: id, request_id, status, reason, decided_at, decided_by.

### `workflow.py`

- `FileProfile` — per-uploaded-file schema: columns (name, dtype, null_rate, sample_values), row_count, encoding, ambiguities.
- `ColumnProfile` — per-column.
- `BusinessRule` — typed rule (predicate over a row producing a value or flag).
- `AuthorRequirements` — confirmed spec: description, input_files (refs), expected_output_columns, business_rules, edge_cases, validation_checks, template_used.
- `RepairProblem` — user's report: report_text, classification (`TEST_FAILURE | RUNTIME_ERROR | WRONG_OUTPUT | PERFORMANCE | OTHER`), confidence.
- `AgentSummary` — plain-English description of an existing agent: purpose, inputs, outputs, entry_point, dependencies.
- `ReproductionResult` — was the bug reproducible? `{ reproduced: bool, method: pytest|sample, evidence, observation_id }`.
- `Diagnosis` — typed: suspected_file, suspected_lines, root_cause, severity, fix_risk, confidence.
- `PatchProposal` — file, unified_diff, rationale.

### `validation.py`

- `ValidationCheck` — per-check outcome: name, layer, passed, evidence, hint_if_failed.
- `ValidationReport` — bundle of checks: session_id, layers, overall_status, generated_at.
- `RepairReport` — six-section structured: problem, reproduction, diagnosis, patches, validation, remaining_risks, next_steps.

### `artifact.py`

- `UploadedFile` — id, session_id, filename, mime, size_bytes, hash_sha256, storage_path, uploaded_at.
- `Artifact` — generated output: type (`GENERATED_CODE | VALIDATION_REPORT | REPAIR_REPORT | ARCHIVE`), path, hash, size, created_at.
- `ExecutionObservation` — subprocess result: success, exit_code, stdout_excerpt, stderr_excerpt, files_written, latency_ms, truncated.
- `TestResults` — pytest result: passed_count, failed_count, summary, per_test (name, status, latency, output_excerpt).

### `eval.py`

- `EvalScenarioKind` (StrEnum) — `AUTHOR | REPAIR | ADVERSARIAL`.
- `EvalScenario` — discriminated union by `kind`:
  - `AuthorScenario` — template, input_files, workflow_description, expected_output_files, expected_validation_layers, expected_terminal_status.
  - `RepairScenario` — fixture, problem_report_path, expected_reproduction, expected_diagnosis, expected_patch, expected_after_fix, expected_terminal_status.
  - `AdversarialScenario` — subkind, payload, location, workflow_description, expected_behaviour, expected_terminal_status.
- `EvalRunResult` — per-scenario: scenario_id, passed, latency_ms, cost_usd, failure_reason.
- `EvalRunSummary` — aggregate: started_at, completed_at, total, passed, failed, per_tag.

### `approval.py`

(See `tool.py` — `ApprovalRequest` and `ApprovalDecision` are defined there; cross-referenced from approval-flow modules.)

### `__init__.py`

Re-exports the canonical names. Importers always use `from agentforge.schemas import X`.

## 2. TypeScript mirrors

Live under `packages/shared-schemas/src/`. The HTTP request/response surface is generated from `apps/api/openapi.snapshot.json` by `make gen-schemas`; hand-written mirrors remain for internal schemas that are not directly exposed through OpenAPI.

Files: `common.ts`, `session.ts`, `event.ts`, `tool.ts`, `workflow.ts`, `validation.ts`, `artifact.ts`, `eval.ts`, `approval.ts`, `index.ts` (re-exports).

The frontend imports types from `@agentforge/shared-schemas`. It never invents types. Drift between Python and TS is a submission defect.

## 3. Event log schema (`events.jsonl`)

One event per line, JSON object:

```
{
  "id": "evt_<uuid>",
  "session_id": "<uuid>",
  "ts": "<ISO-8601>",
  "step": <int>,
  "kind": "<EventKind>",
  "payload": { typed by kind },
  "prev_event_id": "evt_<uuid>" | null
}
```

`EventKind` enum (locked):

```
WORKFLOW_STARTED
WORKSPACE_ALLOCATED
TEMPLATE_SEEDED
FILE_UPLOADED
SCHEMA_DETECTED
DECISION_INPUT
QUESTION_ASKED
ANSWER_RECEIVED
REQUIREMENTS_DRAFTED
REQUIREMENTS_CONFIRMED
PHASE_TRANSITIONED
MODEL_CALLED
TOOL_INVOKED
TOOL_OBSERVED
FILE_WRITTEN
PATCH_APPLIED
APPROVAL_REQUESTED
APPROVAL_GRANTED
APPROVAL_DECLINED
EXECUTION_STARTED
EXECUTION_COMPLETED
EXECUTION_FAILED
TEST_RUN_STARTED
TEST_RUN_COMPLETED
VALIDATION_RUN
ARTIFACT_GENERATED
BUDGET_WARNED
BUDGET_EXHAUSTED
REPAIR_PROBLEM_RECEIVED
AGENT_SUMMARY_PRODUCED
REPRODUCTION_RESULT
DIAGNOSIS_PRODUCED
PATCH_PROPOSED
REPAIR_REPORT_GENERATED
WORKFLOW_COMPLETED
WORKFLOW_FAILED
SESSION_AUTO_ARCHIVED
```

Each kind has a typed payload class. Adding a new kind requires an ADR.

## 4. Tool registry shape

```
ToolDefinition = {
  name: str                 # unique
  description: str          # what the model sees
  input_schema_name: str    # Pydantic class name in agentforge.schemas
  output_schema_name: str
  risk_level: 'read' | 'low_write' | 'high_write'
  requires_approval: bool
  idempotent: bool
  phases: list[str]         # workflow-phase enum values where this tool is enabled
  authorize: callable_name  # function that raises ForbiddenError on deny
  adr_override: str | None  # ADR ID if requires_approval=False on a write tool
}
```

The 20 tools (defined in ARCHITECTURE.md §6.4):

| Tool | risk | approval | idempotent | phases |
|---|---|---|---|---|
| `list_workspace` | read | no | yes | all |
| `inspect_file` | read | no | yes | all |
| `inspect_csv_schema` | read | no | yes | author.info, repair.info |
| `inspect_xlsx_schema` | read | no | yes | author.info, repair.info |
| `seed_template` | low_write | auto | yes | author.info |
| `ask_user` | read | no | yes | all |
| `write_file` | low_write | yes | yes (per idempotency_key) | author.build, repair.fix |
| `apply_patch` | low_write | yes | yes | author.build, repair.fix |
| `run_python_script` | high_write | yes | yes | author.build, repair.* |
| `run_pytest` | high_write | yes | yes | author.build, repair.* |
| `validate_output` | read | no | yes | author.build |
| `summarise_agent_purpose` | read | no | yes | repair.info |
| `classify_problem` | read | no | yes | repair.info |
| `record_reproduction` | read | no | yes | repair.info |
| `diagnose` | read | no | yes | repair.info |
| `propose_patch` | read | no | yes | repair.info |
| `generate_validation_report` | low_write | auto | yes | author.build |
| `generate_repair_report` | low_write | auto | yes | repair.fix |
| `archive_workspace` | low_write | yes | yes | author.build, repair.fix |
| `finalise_session` | low_write | yes | yes | author.build, repair.fix |

Idempotency key: `sha256(session_id + tool_name + step + canonical_json(args))`.

## 5. Workflow phase names

Locked enum values (see `common.py` AuthorPhase, RepairPhase). Renames require an ADR.

## 6. Workspace paths

| Artifact type | Path |
|---|---|
| Manifest | `${workspace_path}/manifest.json` |
| Event log | `${workspace_path}/events.jsonl` |
| Uploads | `${workspace_path}/uploads/${filename}` |
| Generated agent | `${workspace_path}/generated/` |
| Working agent (repair) | `${workspace_path}/working/` |
| Outputs | `${workspace_path}/outputs/` |
| Overflow logs | `${workspace_path}/outputs/_logs/${step}.log` |
| Reports | `${workspace_path}/reports/` |
| Archive | `${workspace_path}/archive.zip` |

Path validation: any tool argument that names a path is resolved against `${workspace_path}` using `Path.resolve()` and rejected if the result does not have `${workspace_path}` as a prefix.

## 7. Validation report format

### `ValidationReport` (author flow)

Markdown structure (in `reports/validation_report.md`):

```
# Validation Report — Session ${session_id}

Generated: ${generated_at}

## Overall: PASS | FAIL

## Layer 1: Schema
- Status: PASS | FAIL
- Evidence: <one-line summary>
- Details: <expandable>

## Layer 2: Required columns
...

## Layer 3: Business rules
...

## Layer 4: Row-level
...

## Layer 5: Golden output
...

## Layer 6: Generated pytest
...
```

JSON sidecar (`reports/validation_report.json`):

```
{ "session_id": ..., "overall_status": ..., "layers": [
  { "name": ..., "passed": ..., "evidence": ..., "hint_if_failed": ... }
] }
```

### `RepairReport` (repair flow)

Six sections, fixed order:

1. **Problem statement** (verbatim from user)
2. **Reproduction** (test name, evidence, ReproductionResult ID)
3. **Diagnosis** (file, line, root_cause, severity, fix_risk, confidence)
4. **Files changed** (per file: hunks count, diff hashes, summary)
5. **Validation** (before-fix pytest summary, after-fix pytest summary, golden-diff result if applicable)
6. **Remaining risks and next steps**

JSON sidecar same structure.

## 8. User-facing status format (UX language map)

Locked verbatim from final decision document §9. Every UI message uses exactly these strings (or a clearly named template that the frontend's `ux-language.ts` maps event kinds to). Drift is a defect.

Examples:

| Event | Primary user-facing message |
|---|---|
| File uploaded | `Received {filename} ({row_count} rows).` |
| File profile complete | `We've examined the file. It has {column_count} columns and looks consistent.` |
| Missing column | `We can't find a column that looks like a {missing_role}. Which column has the {missing_role}?` |
| Ambiguous column | `We see {field}, but they could be {format_a} or {format_b}. Which one does your file use?` |
| Requirements inferred | `Here's what we understood. Review or correct anything before we build the agent.` |
| Clarification needed | `Quick question: {plain_english_question}` |
| Code generated | `We've drafted the agent. In plain English, here's what it does.` |
| Tests generated | `We've added {test_count} automatic checks so you can rerun this any time and know it's still correct.` |
| Run started | `Running the agent on your sample.` |
| Run failed | `The agent's code stopped before finishing. We'll look at why.` |
| Validation passed | `All checks passed. The output looks correct.` |
| Validation failed | `One check failed: {human_check_name}. {one_line_evidence}.` |
| Output ready | `Your agent processed all {row_count} rows and produced {output_filename}.` |
| Repair triage complete | `Here's what your agent does, based on the code: {summary}. Does this match?` |
| Failure reproduced | `We confirmed the problem. The check '{humanised_test_name}' failed exactly as you described.` |
| Root cause found | `We think the issue is in {file}, around line {line} — {plain_english_root_cause}.` |
| Patch proposed | `We have a fix: {plain_english_fix}. Here's the technical change.` |
| Patch applied | `Fix applied to {file}.` |
| Repair validated | `We re-ran the checks. All {pass_count} pass now. {numeric_evidence}.` |
| Report ready | `Repair report ready. You can review it or download the repaired agent.` |
| Session paused | `Saved. You can come back to this session any time in the next 7 days.` |
| Session resumed | `Welcome back. Picking up where you left off: {phase_name}.` |
| Command timed out | `The agent's code ran for longer than allowed and was stopped. The output up to that point is saved.` |
| Malformed file detected | `We couldn't read {filename} — it doesn't look like a valid CSV.` |

## 9. Versioning policy

- **Additive changes** within a build phase (new optional field, new enum variant, new event kind) require a new ADR but no migration. `manifest.schema_version` is unchanged.
- **Breaking changes** (rename, type change, removal, required field added) require:
  - A new ADR with explicit migration plan and supersession of the prior ADR.
  - `manifest.schema_version` increment.
  - The session-resume path detects schema_version mismatch and surfaces "session was created on an older version" instead of crashing.

Historical ADRs are never edited. Supersession is forward-only.
