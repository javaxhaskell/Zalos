# AgentForge — Runbook

> What to do when something goes sideways. Each entry maps a symptom to the diagnostic path through the codebase, the typical fix, and the audit-trail location that confirms it.

---

## Failure modes catalog

The agent loop is bounded (INV-12) and every failure goes through a typed error code. The full enum lives in [`apps/api/src/agentforge/schemas/common.py`](./apps/api/src/agentforge/schemas/common.py) under `ErrorCode`.

| Error code | Surface | What it means | What to do |
|---|---|---|---|
| `budget_exhausted_tokens` | `failed_budget` row + FailureCard | Agent burned through `BUDGET_TOKENS`. | Smaller sample input, or bump `BUDGET_TOKENS` for the session. |
| `budget_exhausted_steps` | `failed_budget` row | Agent ran the full step cap without finishing. | Inspect the events.jsonl: usually the model is looping on the same tool. The same-tool-same-args circuit breaker should have fired; if not, file a bug. |
| `budget_exhausted_wall_time` | `failed_budget` row | Wall clock exceeded `BUDGET_WALL_SECONDS`. | Usually a subprocess that took too long. Reduce the input size or raise the cap. |
| `budget_exhausted_tool_calls` | `failed_budget` row | Tool-call cap hit. | Inspect events for the over-called tool. |
| `validation_loop_exhausted` | `failed_model` row | Three consecutive Pydantic re-prompts failed. | The model's output drifted from the tool schema. Inspect `tool_invoked` + `tool_observed` events for the validation_failed observation. |
| `generated_code_failed` | `failed_other` (BP10 maps this) | The agent's generated script crashed on the sample. | Open `outputs/_logs/<step>.log` for the traceback. |
| `test_failed` | (informational; not terminal) | A test in `working/tests/` or `generated/tests/` failed during a run. | Read `events.jsonl` for the `test_run_completed` event's `per_test` list. |
| `command_timeout` | `failed_sandbox` row | A subprocess exceeded `SUBPROCESS_TIMEOUT_*`. | The sample CSV is too big or the agent is slow. |
| `repair_cannot_reproduce` | `failed_other` | Repair flow's INFO phase couldn't reproduce the reported failure. | The problem report doesn't describe a reproducible failure, or there's no pytest in `working/`. |
| `approval_declined` | `failed_user_reject` | The user declined an approval. | Loop back to the diagnose / requirements step; re-prompt the model with the decline reason. |
| `unknown` | `failed_other` | Anything else — including unhandled exceptions in the agent loop. | Open the audit log; ping support. |

The FailureCard component in the wizard surfaces the humanised version + a suggested next action.

---

## How to read `events.jsonl`

Every session has a workspace at `${WORKSPACES_ROOT}/<session_uuid>/` with an `events.jsonl` file at the root. One event per line, JSON. The chronological event chain is the source of truth for what happened (INV-6).

```bash
# Quick scan: kinds in chronological order
jq -r '.kind' events.jsonl | nl

# Just the workflow-level events
jq 'select(.kind | startswith("workflow_") or startswith("phase_"))' events.jsonl

# The model's reasoning trace
jq 'select(.kind == "model_called") | {step, response_id: .payload.response_id, stop_reason: .payload.stop_reason, total_tokens: .payload.usage.total_tokens}' events.jsonl

# Every tool the loop dispatched
jq 'select(.kind == "tool_invoked") | {step, tool: .payload.tool_name, args_hash: .payload.args_hash}' events.jsonl

# Find the failure
jq 'select(.kind == "workflow_failed") | .payload' events.jsonl

# Verify the chain (prev_event_id linkage)
python -c "
import json, sys
prev = None
for i, line in enumerate(open('events.jsonl')):
    e = json.loads(line)
    assert e.get('prev_event_id') == prev, f'chain break at line {i+1}: prev was {e.get(\"prev_event_id\")}, expected {prev}'
    prev = e['id']
print('chain intact:', i+1, 'events')
"
```

The `GET /audit/export/{session_id}` endpoint returns the full chain + the manifest + a `chain_check.valid` boolean.

---

## How to read `repair_report.md`

Six sections in fixed order (per [`CONTRACTS.md`](./CONTRACTS.md) §7):

1. **Problem statement** — verbatim from the user.
2. **Reproduction** — test name + evidence + observation id.
3. **Diagnosis** — file, line range, root cause, severity, fix risk, confidence.
4. **Files changed** — per file: hunks count, diff hash, summary.
5. **Validation** — before-fix pytest summary + after-fix pytest summary + golden-diff result.
6. **Remaining risks and next steps**.

The corresponding JSON sidecar at `reports/repair_report.json` is the structured form. The wizard's RepairReportCard renders the JSON; the markdown is the auditable artifact.

If the report's "Validation after-fix" doesn't show 3/3 pass for `invoice_aging_v1`, the patch didn't land or the date-format flip went the wrong direction. Inspect `working/agent.py` line 34.

---

## How to read `validation_report.md`

Six layers (ADR-0007):

1. **Schema** — output CSV has the expected columns.
2. **Required columns** — non-null in every row.
3. **Business rules** — domain-specific predicates (e.g., refunds tagged as Refund).
4. **Row-level** — per-row sanity checks.
5. **Golden output** — row-aligned diff against `evals/golden_output.csv` (or staged golden).
6. **Generated pytest** — run the template's tests against the agent.

Each layer is `pass | fail | skipped`. Overall is `PASS` iff all non-skipped layers pass. The markdown carries the layer evidence; the JSON sidecar is the structured form.

---

## Common 4xx responses (and what they mean)

| HTTP | Endpoint | error_code | Meaning + fix |
|---|---|---|---|
| 404 | `GET /sessions/{id}` | `unknown` | Session row doesn't exist. Check the UUID. |
| 404 | `POST /sessions/{id}/run` | (none — APIError default) | Session row doesn't exist. |
| 409 | `POST /sessions/{id}/run` | `session_state_conflict` | The session is already RUNNING or COMPLETED. Inspect `current_status` in the response detail; wait for the bg task or start a new session. INV-7 idempotency. |
| 409 | `POST /sessions/{id}/finalise` | `finalise_without_artifact` | No `ARTIFACT_GENERATED` event on record. The agent must call `generate_validation_report` or `generate_repair_report` first. |
| 422 | `POST /sessions/{id}/answer` | (validation) | Empty answer string. Provide non-empty text. |
| 415 | `POST /sessions/{id}/files` | `unsupported_file_type` | Only .csv / .xlsx accepted. |
| 413 | `POST /sessions/{id}/files` | `file_too_large` | Single-file cap exceeded (default 25 MB). |
| 413 | `POST /sessions/{id}/files` | `upload_limit_exceeded` | Per-session total cap exceeded (default 100 MB). |
| 400 | `POST /sessions/{id}/load_fixture/{name}` | `invalid_fixture_name` | Name contains chars outside `[A-Za-z0-9_-]`. Path traversal attempt. |
| 404 | `POST /sessions/{id}/load_fixture/{name}` | `fixture_not_found` | No fixture directory of that name under `FIXTURES_BROKEN_AGENTS_ROOT`. |
| 409 | `POST /sessions/{id}/load_fixture/{name}` | `working_not_empty` | The `working/` dir already has files. Start a new session to switch fixtures. |
| 403 | `GET /sessions/{id}/artifacts/{path}` | `artifact_path_forbidden` | Path doesn't start with an allow-listed prefix (`reports/`, `outputs/`, `generated/`, `working/`). |
| 404 | `GET /sessions/{id}/artifacts/{path}` | `artifact_not_found` | File doesn't exist at the resolved workspace path. |
| 409 | `GET /sessions/{id}/archive.zip` | `archive_empty` | The session produced no load-bearing artifacts (no `generated/`, `working/`, `outputs/`, or `reports/` contents). Run the agent first. |
| 404 | `GET /evals/latest` | `no_eval_runs` | No `EvalRunRow` rows yet. `POST /evals/run` first. |
| 503 | `GET /health/ready` | (checks) | `LLM_PROVIDER=ollama` but Ollama is down or a configured model is not pulled. Start `ollama serve`; for the local split, run `ollama pull qwen2.5-coder:14b` and `ollama pull qwen2.5-coder:7b`. |

---

## Broken-agent fixtures (Repair workflow)

Two synthetic broken-agent fixtures ship in `fixtures/broken_agents/`:

### `invoice_aging_v2` (recommended — boundary bug)

A 20-row invoice-aging agent. The bug is a one-line off-by-one:

```python
# fixtures/broken_agents/invoice_aging_v2/agent.py
if days_overdue <= 31:    # bug — should be <= 30
    return ("1-30", "low")
```

Invoices that are exactly **31** days overdue land in the **1-30**
bucket instead of **31-60**. The bundled sample contains three such
invoices (`INV-0005`, `INV-0013`, `INV-0018`).

**Reproduce the failure manually:**

```bash
cd fixtures/broken_agents/invoice_aging_v2
python3 -m pytest tests/ -q
# Expected: 5 passed, 2 failed
#   FAILED tests/test_agent.py::test_boundary_31_days_in_31_60_bucket
#   FAILED tests/test_agent.py::test_output_matches_expected_output_csv
```

**Verify the canonical repair works:**

```bash
sed -i.bak 's/days_overdue <= 31:/days_overdue <= 30:/' \
  fixtures/broken_agents/invoice_aging_v2/agent.py
cd fixtures/broken_agents/invoice_aging_v2
python3 -m pytest tests/ -q
# Expected: 7 passed
mv agent.py.bak agent.py   # restore the broken version
```

**Repair through the UI:**

1. Dashboard → **Repair an existing agent**.
2. Pick **Invoice aging v2 (boundary bug)** — labelled *Built-in
   synthetic repair fixture*.
3. Click **Load fixture** (copies the broken agent into
   `working/`).
4. Paste a problem report (or use the bundled
   `problem_report.md`).
5. Click **Start agent**. The repair workflow reproduces, diagnoses,
   patches, re-tests, and writes `reports/repair_report.md` plus
   the archive.

**Backend regression** (proves the fixture is genuinely broken and
the canonical fix works):

```bash
cd apps/api
uv run pytest tests/test_repair_fixture_invoice_aging_v2.py -v
# 6 passed
```

### `invoice_aging_v1` (historical — date-format bug)

Date format mismatch (DD-MM-YYYY vs MM-DD-YYYY). 3 tests; 2 pass /
1 fail before repair. Used by `test_repair_flow_e2e.py` and the
eval scenarios; left in place for regression continuity.

---

## Ollama (local LLM)

When `LLM_PROVIDER=ollama`:

1. **Readiness:** `curl http://localhost:8000/health/ready` — `checks.ollama` must be ok; otherwise HTTP 503.
2. **Workflow failures (`failed_model`):** Inspect `events.jsonl` for the step before terminal; the API returns `ModelClientError` text such as `cannot reach Ollama` or `model 'qwen2.5-coder:14b' not found`.
3. **Fix connectivity:** `ollama serve` (or install the macOS app which runs the daemon).
4. **Fix missing model:** `ollama pull qwen2.5-coder:14b` (match `OLLAMA_MODEL` in `.env`).
5. **Verify:** `curl http://localhost:11434/api/tags` lists your model tag.

Evals (`make eval`) still use scripted `FakeModelClient` — they do not call Ollama. Live author/repair wizards do.

---

## What to do when `/run` returns 409

Two sub-cases:

**`current_status: "running"`** — a previous `/run` is still in flight. Options:
1. Wait. Poll `/sessions/{id}/events?after=<last_event_id>` until you see `workflow_completed` or `workflow_failed`.
2. The previous request may have crashed mid-flight in a way the BP8 supervisor didn't catch. Manually flip the row to `failed_other` via the DB if you're sure:
   ```sql
   UPDATE sessions SET status='failed_other', updated_at=now() WHERE id='<uuid>';
   ```
   Then re-POST `/run`. **This is a last resort** — the supervisor's `except` block should catch all flow exceptions; if it didn't, file a bug with the timestamp so we can find the missing handler.

**`current_status: "completed"`** — the session is done. Start a new session via `POST /sessions`.

---

## Inspecting an `archive.zip`

The archive bundles every load-bearing workspace artifact:

```
manifest.json                     # Session metadata + budget + file_hashes
events.jsonl                      # Append-only event chain (the audit trail)
generated/                        # Author-flow: the produced agent
  agent.py
  rules.py
  tests/
working/                          # Repair-flow: the post-patch agent
  agent.py
  ...
outputs/                          # Subprocess outputs
  output.csv
  _logs/<step>.log                # stdout overflow when > 1 MiB
reports/                          # Generated artifacts
  validation_report.md
  validation_report.json
  repair_report.md
  repair_report.json
```

`uploads/` is **deliberately excluded** — the user uploaded those, they already have them. If you need the input, fetch it from the session's pre-archive workspace.

```bash
unzip -l agentforge-session-<uuid>.zip
# Or:
python -c "
import zipfile
z = zipfile.ZipFile('agentforge-session-<uuid>.zip')
for info in z.infolist():
    print(f'{info.file_size:>10}  {info.filename}')
"
```

---

## Budget exhaustion recovery

When a session lands in `failed_budget` with `terminal_error_code: budget_exhausted_*`, the workspace is still intact. To recover:

1. Inspect the audit log. Look for the loop / repeated tool / unexpectedly-large model response that drove usage.
2. Decide whether to extend the budget (set the env var + restart the API) or to redo the session with a smaller input.
3. Start a new session — there's no "resume after budget exhaustion" path in the prototype; the per-session caps are hard limits.

For a smaller input, the bank-categoriser sample is 200 rows. The invoice-aging sample is 30 rows. Both fit comfortably under the default 150k token cap; if a customer's real CSV is bigger, chunk it before upload.

---

## When the agent says it can't reproduce a failure

Repair flow's INFO phase calls `run_pytest` on `working/tests/`. If no tests are collected (or every test passes), the agent emits a `REPRODUCTION_RESULT` with `reproduced=False` and the orchestrator transitions to `repair_need_info`:

- If the user's problem report describes a runtime crash without a test, the agent should fall back to `run_python_script` to reproduce via the sample input. Today the `invoice_aging_v1` fixture happens to have a failing pytest, so this path is the happy default.
- If the agent can't reproduce, the wizard surfaces the "we couldn't reproduce" copy from CONTRACTS.md §8.

---

## Re-opening a paused session

The `paused_user` and `paused_approval` statuses are intentionally non-terminal. The user navigates back to `/author/<sid>` (or `/repair/<sid>`) and sees the ResumeBanner ("Welcome back. The agent is paused …"). The pending question or approval surfaces below. After answering / approving, click Start again — the agent loop picks up from the next step.

If the user never returns: the prototype doesn't auto-archive paused sessions. ADR-0010 documents the 24h-idle-archive policy as a production extension.

---

## When `make eval` reports a scenario failed

The `EvalResultRow.failure_reason` carries the terminal-status mismatch (e.g., `terminal_status=failed_model, expected=completed`). Find the per-scenario workspace in the test's tmpdir; inspect its `events.jsonl` for the underlying failure event. The eval runner's `_run_one` allocates each scenario its own session workspace, so the trail is preserved.

If a scenario fails because of a script drift (the scripted FakeModelClient stopped matching what the orchestrator expects), the fix is in [`apps/api/src/agentforge/evals/scripts.py`](./apps/api/src/agentforge/evals/scripts.py). Don't soften the scenario JSON to make a flaky agent pass — `evals/EVAL_RUBRIC.md` calls this out as a forbidden pattern.

---

## Local dev daemon

Use the background daemon when you want API + web to keep running after a terminal or Cursor agent session ends.

| Command | Purpose |
|---|---|
| `make up-daemon` | `migrate` + API (:8000) + web (:3000) via `nohup` |
| `make down` | Stop supervisor and free ports |
| `make dev-status` | PID file + port listeners |
| `make dev-health` | HTTP check on `/health` and `/` |
| `make dev-logs` | `tail -f /tmp/agentforge-dev.log` |

PID file: `/tmp/agentforge-dev.pid`. Log: `/tmp/agentforge-dev.log`.

If `make up-daemon` refuses to start because ports are already taken, run `make down` first (it stops both daemon-managed and orphan listeners on 8000/3000).

**macOS login auto-start (optional):**

```bash
REPO="$(pwd)"
sed "s|REPLACE_WITH_REPO|$REPO|g" scripts/com.agentforge.dev.plist > ~/Library/LaunchAgents/com.agentforge.dev.plist
launchctl load ~/Library/LaunchAgents/com.agentforge.dev.plist
```

Unload with `launchctl unload ~/Library/LaunchAgents/com.agentforge.dev.plist`. The plist runs `make up-daemon` once at login; it does not restart on crash (`KeepAlive` is false) so failed boots stay visible in `/tmp/agentforge-dev.launchd.log`.

---

## Where to find things

| Question | Answer |
|---|---|
| What happened in this session? | `${WORKSPACES_ROOT}/<sid>/events.jsonl` or `GET /audit/export/{sid}` |
| What did the agent produce? | `${WORKSPACES_ROOT}/<sid>/{generated,working,outputs,reports}/` or `GET /sessions/{sid}/archive.zip` |
| Why did it stop? | Last `workflow_completed` or `workflow_failed` event; FailureCard in the UI |
| What tools were dispatched? | `jq 'select(.kind=="tool_invoked")' events.jsonl` |
| How much budget remains? | `GET /sessions/{sid}` → `budget` field; or watch the live BudgetBanner in the wizard |
| Was the chain tampered with? | `GET /audit/export/{sid}` → `chain_check.valid` |
| Which tool registered which schema? | `apps/api/src/agentforge/tools/registry.py::build_registry()` |
| What's the OpenAPI surface? | `apps/api/openapi.snapshot.json` (1959 lines as of BP11) |
| Where does the policy say what? | `.claude/skills/agentforge-thesis-keeper/SKILL.md` (12 invariants) |
