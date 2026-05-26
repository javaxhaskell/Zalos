# AgentForge — Workflows

> Status: final submission snapshot. Owned by `agentforge-architect`. The two workflow tables below describe the implemented prototype flow and the intended state-transition evidence.

## Workflow 1 — Author a new agent

12 user-visible steps. State transitions in `orchestrator/state_machine.py` enforce these.

| # | User step | System step | Tools called | Files read | Files written | Outputs / persisted state | Validation evidence | Failure states |
|---|---|---|---|---|---|---|---|---|
| 1 | Click "Author new agent" | Create session row; allocate workspace; `git init` inside `${workspace_path}`; emit `WORKFLOW_STARTED` + `WORKSPACE_ALLOCATED` | (none — orchestrator) | (none) | `manifest.json`, `events.jsonl`, empty `uploads/` | session row in SQLite + 2 events | none yet | workspace dir creation fails → `failed_other` |
| 2 | Pick "Bank Transaction Categoriser" template (or skip) | Copy template files into `generated/`; `git commit "template seed"`; emit `TEMPLATE_SEEDED` | `seed_template` | template package | `generated/agent.py`, `generated/rules.py`, `generated/tests/test_agent.py`, `generated/requirements.txt` | tool_invocation + git commit | none yet | unknown template → fall back to scratch (no error) |
| 3 | Drag-drop CSV/XLSX | Hash file (sha256); write to `uploads/`; emit `FILE_UPLOADED` | (upload endpoint, not a tool) | (none) | `uploads/${filename}` | uploaded_files row; manifest hash entry | file size/MIME validated at endpoint | `file_too_large` / `upload_limit_exceeded` / `unsupported_file_type` → reject at upload |
| 4 | (waits) | Profile each uploaded file | `inspect_csv_schema` or `inspect_xlsx_schema` | `uploads/*` | (none) | `FileProfile` in events log | per-column dtype + null rate visible in SchemaTable | `malformed_csv` / `ambiguous_schema` → surface to user |
| 5 | Read schema; type workflow description | Surface description input | (none) | `uploads/*` | (none) | `DECISION_INPUT` event with description text | none | empty description → require |
| 6 | Submit description | The model profiles files and decides whether it has enough context to build | `inspect_csv_schema` / `inspect_xlsx_schema`, `list_workspace`, `inspect_file` | `uploads/*` | (none) | schema/profile observations in the event log | profile visible in events | conflicting inputs → automatic transition to step 7 (QA) |
| 7 | Answer 0–3 clarifying questions | Pause after each question; resume on answer; continue with the answer in model context | `ask_user` (pauses) | (none) | (none) | `QUESTION_ASKED` / `ANSWER_RECEIVED` events | none | user abandons → `auto_archived` at 24h idle |
| 8 | Review Requirements card; approve | Persist confirmed `AuthorRequirements`; transition `info` phase → `build` phase | (approval endpoint) | (none) | `manifest.json` (phase update) | `REQUIREMENTS_CONFIRMED` + `APPROVAL_REQUESTED` + `APPROVAL_GRANTED` events | none yet | user rejects → loop back to step 5/7 (cap 2 iterations) |
| 9 | (waits) | Generate code via tool dispatch; lint after each write | `write_file`, `apply_patch` (multiple); implicit lint after each | template files | `generated/agent.py`, `generated/rules.py`, `generated/tests/test_agent.py` (modified) | tool_invocations; git commits in `generated/`; `FILE_WRITTEN` / `PATCH_APPLIED` events | lint clean | model emits invalid edit → one re-prompt then `validation_loop_exhausted`; bounded by step cap |
| 10 | Approve generated diff | Mark diff approved | (approval endpoint) | (none) | manifest update | `APPROVAL_REQUESTED` + `APPROVAL_GRANTED` (or `APPROVAL_DECLINED`) | none yet | declines → loop back to step 9 (cap 2 iterations) |
| 11 | (waits) | Execute generated agent on uploaded sample; run generated pytest; run validation checks | `run_python_script`, `run_pytest`, `validate_output` | `generated/*`, `uploads/*` | `outputs/*` (CSVs the agent produces); `outputs/_logs/*` for large stdout | `EXECUTION_*` events; `TEST_RUN_*` events; `VALIDATION_RUN` event; `ValidationReport` (six layers — see §8 of decision) | full six-layer `ValidationReport` | `command_timeout` / `generated_code_failed` / `test_failed` → surface; loop back to step 9 (cap 2 iterations) |
| 12 | Review outputs + report; click Finalise | Build archive + README inside workspace; mark `completed` | `generate_validation_report`, `archive_workspace`, `finalise_session` | all session files | `reports/validation_report.md`, `reports/README.md`, `archive.zip` | `ARTIFACT_GENERATED`, `WORKFLOW_COMPLETED` events; session row → `completed` | final `ValidationReport` + downloadable ZIP | archive fail → `failed_other` |

## Workflow 2 — Repair an existing agent

13 user-visible steps.

| # | User step | System step | Tools called | Files read | Files written | Outputs / persisted state | Validation evidence | Failure states |
|---|---|---|---|---|---|---|---|---|
| 1 | Click "Repair existing agent" | Create session row; allocate workspace; `git init`; emit `WORKFLOW_STARTED` + `WORKSPACE_ALLOCATED` | (none) | (none) | `manifest.json`, `events.jsonl` | session row + events | none | workspace fail |
| 2 | Upload ZIP (or pick bundled `invoice_aging_v1` fixture) | Extract/copy into `working/`; stage `data/expected_output.csv` to `evals/` when present; emit `DECISION_INPUT` + `FILE_UPLOADED` events | upload/extraction endpoint or bundled-fixture loader | uploaded zip or bundled fixture | `working/*`, optional `evals/expected_output.csv` | event log records `fixture_loaded` or `agent_zip_uploaded` decision | extraction succeeded; file tree shown | corrupt zip / zip-slip / too many files / oversized archive / missing entry point → surface |
| 3 | Type problem report | Persist; classify | `classify_problem` | (none) | (none) | `RepairProblem` typed in events; `REPAIR_PROBLEM_RECEIVED` | classification visible | empty report → require |
| 4 | (waits) | Inspect files; produce `AgentSummary` (plain-English) | `list_workspace`, `inspect_file` (multiple), `summarise_agent_purpose` | `working/*` | (none) | `AGENT_SUMMARY_PRODUCED` event with `AgentSummary` | plain-English summary visible | unreadable files → degrade to entry-point-only summary |
| 5 | Review summary; confirm or correct | Persist confirmation (or correction as new `DECISION_INPUT`) | (approval endpoint) | (none) | (none) | `APPROVAL_REQUESTED` + `APPROVAL_GRANTED` (or correction loop) | confirmed summary | user says "no" → loop back to step 4 with correction (cap 2 iterations) |
| 6 | (waits) | Run existing tests; if no tests collected, run a sample execution | `run_pytest` (first; if "no tests collected", then `run_python_script`) | `working/*` | event log; `outputs/_logs/*` | `TEST_RUN_*` or `EXECUTION_*` events; `REPRODUCTION_RESULT` typed object | reproduction observation: which test failed + error excerpt | tests don't exist + no entry-point script → `repair_need_info` (paused for user) |
| 7 | Approve "Run tests on the agent" if first time | (already approved if step 6 was covered) | (none) | (none) | approval_request if needed | (covered by step 6 approval if same session) | reproduction surfaced | declined → terminal `failed_user_reject` |
| 8 | (waits) | Diagnose with structured output (requires `REPRODUCTION_RESULT` in event log) | `inspect_file` (relevant files), `diagnose` | `working/*` | (none) | `DIAGNOSIS_PRODUCED` event with `Diagnosis` (file, lines, root_cause, severity, fix_risk, confidence) | diagnosis visible | low confidence → surface "low confidence; expert review needed" (still proceeds to step 9 with caveat) |
| 9 | (waits) | Propose patch with rationale | `propose_patch` | `working/*` | (none) | `PATCH_PROPOSED` event with `PatchProposal` (file, unified_diff, rationale) | proposal visible | no clear fix → surface "needs human help"; user can edit problem and re-trigger |
| 10 | Review diff + plain-English explanation; approve | Apply patch via `git apply`; lint changed file | `apply_patch` | `working/*` | `working/*` (modified file + new git commit) | tool_invocation; commit hash; `PATCH_APPLIED` event | patch applied evidence | declines → loop back to step 8 (cap 2 iterations) |
| 11 | (waits) | Re-run tests; re-run sample; compare to `data/expected_output.csv` (if fixture provides) | `run_pytest`, `run_python_script`, `validate_output` | `working/*`, fixture data | event log; output files | before/after `TestResults`; golden diff result | full six-piece repair evidence: reproduction + failing test + root cause + patch + after-fix run + expected-output diff | tests still fail → loop back to step 8 (cap 2 iterations); after cap → surface `failed_user_reject` or "needs human help" |
| 12 | Review repair report | Generate `RepairReport` | `generate_repair_report` | event log | `reports/repair_report.md` | `REPAIR_REPORT_GENERATED` event; `RepairReport` persisted | full structured `RepairReport` (six sections) | (none material) |
| 13 | Click Finalise | Build archive; mark `completed` | `archive_workspace`, `finalise_session` | all session files | `archive.zip` | `ARTIFACT_GENERATED`, `WORKFLOW_COMPLETED` events; session row → `completed` | `repair_report.md` + archive ZIP | archive fail → `failed_other` |

## Cross-cutting rules

1. **No transition into `*_diagnose` without a recorded `REPRODUCTION_RESULT` event.** Enforced in `state_machine.py`.
2. **No transition into `*_applied` / `*_apply` without a recorded `APPROVAL_GRANTED` event for that step.** Enforced in the agent loop and the state machine.
3. **No transition into `completed` without a recorded `ARTIFACT_GENERATED` event for the appropriate artifact** (`generated/agent.py` for author; `reports/repair_report.md` for repair).
4. **No transition out of `paused_*`** without a recorded user input event (`ANSWER_RECEIVED` for `paused_user`; `APPROVAL_GRANTED` or `APPROVAL_DECLINED` for `paused_approval`).
5. **Max 3 clarifying-question cycles** in author flow (`author_qa`).
6. **Max 2 patch-iteration cycles** in repair flow (`repair_review_patch` → `repair_diagnose`).
7. **Idempotency keys** on every write-tool invocation: `sha256(session_id + tool_name + step + canonical_json(args))`.
8. **Tool-exposure phases** (coarse) gate which tools the registry surfaces to the model at each step (see `ARCHITECTURE.md` §6.4).
