# Sandbox and artifacts

## Sandbox runner

`apps/api/src/agentforge/sandbox/runner.py` is the only subprocess spawn point for tool execution.

| Invariant | Enforcement |
|---|---|
| Workspace boundary (INV-5) | `WorkspaceManager.resolve_in` before spawn |
| Timeout (INV-12) | Always set; SIGKILL on expiry |
| Secret isolation | Minimal env; no `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` inheritance |
| Output bound | 1 MiB per stream; overflow to `outputs/_logs/{step}.log` |

Tools that execute code: `run_python_script`, `run_pytest`, `apply_patch` (pipes diff to `patch`).

## Path discipline

All file-touching tools resolve paths through `WorkspaceManager.resolve_in`:

- Rejects absolute paths
- Rejects `..` escape from session workspace
- Chokepoint for `write_file`, `apply_patch`, uploads, archive

## Reserved paths (backend-managed)

Generated agents and model-authored contracts must not write these paths. The static safety scan in `author_llm_authoring.py` blocks them before execution:

| Path | Owner |
|---|---|
| `events.jsonl` | EventLog append only |
| `manifest.json` | SessionStore / WorkspaceManager |
| `archive.zip` | `persistence/archive.py` |
| `SESSION_README.md` | Archive builder |
| `generated/author_output_contract.json` | Model authoring pipeline |
| `reports/model_authoring_summary.md` | Backend provenance writer |

Additional forbidden prefixes for generated agents: `generated/model_responses/`, `generated/debug/`.

Contract semantic validation rejects deliverables targeting reserved paths. Tool-layer `write_file` does not block reserved paths explicitly — the safety scan and contract gate cover generated agent code; orchestrator code writes these paths directly.

## Workspace layout

Per session under `${WORKSPACES_ROOT}/{session_id}/`:

```
manifest.json          # resume state, budgets, file hashes
events.jsonl           # append-only audit (INV-6)
uploads/                 # immutable after upload
generated/             # agent.py, contract, tests
outputs/                 # agent-produced CSVs
reports/                 # validation and repair reports
working/                 # repair extracted agent
archive.zip              # terminal bundle
```

Lifecycle rules: [`ARCHITECTURE.md` §5](../ARCHITECTURE.md).

## Event log integrity

- Writes only through `EventLog.append`
- `_last_event_id` raises `EventLogError` if the tail line is malformed (detects corruption from reserved-path overwrites)
- `read_all` raises on unparseable lines
- `verify_chain` checks `prev_event_id` linkage

## Archive contents

`build_archive` includes uploads, generated files, outputs, reports, manifest, events.jsonl, and SESSION_README. Hash recorded in manifest for download verification.

## Tests

| File | Coverage |
|---|---|
| `test_author_codegen_reliability.py` | Reserved path static scan |
| `test_workspace_tools.py` | Path discipline |
| `test_code_tools.py` | write_file / apply_patch |
| `test_architecture_invariants.py` | Event log malformed handling |
