# General Author DeepSeek Reset Audit

Date: 2026-05-25

Commands run:

- `git status --short`
- `git diff --stat`
- `git diff` (captured for audit in `/tmp/zalos_git_diff.txt`)

## Repo State

The worktree already contained a large uncommitted change set before this reset pass. Current changed areas are:

- Provider/config/docs: `.env.example`, `README.md`, `apps/api/src/agentforge/config.py`, `apps/api/src/agentforge/api/lifespan.py`, `apps/api/src/agentforge/api/routers/health.py`, `apps/api/src/agentforge/models/__init__.py`, `apps/api/src/agentforge/models/client.py`, `apps/api/src/agentforge/models/deepseek_client.py`.
- Author backend: `apps/api/src/agentforge/orchestrator/author_llm_authoring.py`, `apps/api/src/agentforge/orchestrator/author_custom_build.py`, `apps/api/src/agentforge/orchestrator/author_contract_validation.py`, `apps/api/src/agentforge/orchestrator/generated_agent_coercion.py`, `apps/api/src/agentforge/orchestrator/workflow_artifacts.py`, `apps/api/src/agentforge/tools/validation_tools.py`, `apps/api/src/agentforge/validation/layers.py`, archive/session/schema files.
- Backend tests: `apps/api/tests/test_author_output_contract_schema.py`, `apps/api/tests/test_author_codegen_reliability.py`, `apps/api/tests/test_author_custom_workflow_gate.py`, `apps/api/tests/test_author_completion_gate.py`, `apps/api/tests/test_generated_pytest_gate.py`, `apps/api/tests/test_validation_layers.py`, `apps/api/tests/test_config_defaults.py`, `apps/api/tests/test_deepseek_provider.py`, `apps/api/tests/test_author_artifact_report_separation.py`, fixtures.
- Frontend: `apps/web/app/**`, `apps/web/src/components/**`, `apps/web/next.config.mjs`, `apps/web/package.json`, `apps/web/tsconfig.json`, bundled bank reference sample route and CSV.
- Shared schemas: `packages/shared-schemas/src/artifact.ts`, `packages/shared-schemas/src/generated.ts`, `packages/shared-schemas/src/session.ts`.
- Local/generated data: `.workspaces-blind-eval/` was already untracked; this run created `.workspaces-deepseek-benchmark/`. Both workspace roots are ignored now.

`git diff --stat` currently reports 69 tracked files changed, about 17.9k insertions and 5.2k deletions, plus multiple untracked files and fixtures.

## General Reliability Fixes To Keep

These changes align with the general LLM-first Author objective and should remain:

- Optional exception outputs are not treated as hard-required unless contract required paths say so.
- Failure manifests derive artifact metadata from emitted `artifact_generated` events.
- JSON hygiene strips fences/comments/trailing commas before parsing while preserving strings.
- Passthrough required output semantics are added only for exact uploaded input column matches.
- Contract-stage token caps/diagnostics remain for local Ollama; DeepSeek omits caps to avoid truncation.
- Contract-shape and schema-dialect sanitizers repair unambiguous shape errors without inventing business logic.
- Artifact path namespace normalization is limited to contract-declared output artifacts.
- Review-output hygiene strips forbidden meta/guidance fields and preserves uploaded schema faithfulness.
- Requested deliverables can justify explicit summary/exception artifacts only when user-explicit.
- Generated-agent guidance covers decimal/date coercion, required artifact production, safety restrictions, contract column-list shape, helper tuple arity, and DictWriter fieldnames.
- Generated pytest remains required and is part of the completion gate.
- Deterministic validation remains required and failed the expense benchmark correctly.
- Failure archives/evidence are still produced without marking failed runs successful.

## Demo-Specific Code

Found bank-reference-specific production-path code:

- Bundled bank reference contract scaffold and golden policy helpers in `author_llm_authoring.py`.
- Bundled bank codegen/test/repair prompt sections in `author_llm_authoring.py`.
- Bundled bank validation observations/checks in `author_custom_build.py`.
- Bundled bank expected output CSV under `apps/web/app/api/reference-samples/bank-categoriser/expected_output.csv`.

Reset action:

- The legacy bank reference scaffold path is now behind `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD=false`.
- Normal Author runs no longer seed the bank contract, fall back to the scaffold on failed planning/review, force bank golden validation, or inject exact bundled row expectations by default.
- Tests that assert the dev scaffold behavior now enable the flag explicitly.

Remaining bank-specific code is isolated/dev/test/reference-sample material. It should not be used for the normal Author path unless the flag is explicitly enabled.

## Bank-Specific Production Logic

No always-on bank-specific Author production logic remains in the normal path after this pass.

Remaining risk:

- Bank-specific helper functions still live beside general Author code. They are gated but should eventually move into test/dev sample support to reduce accidental coupling.
- `validation/layers.py` still has a bank-categoriser semantic layer for template/eval use; it is not wired into the normal custom Author validation path by default.

## Author Flow Shape

The normal Author flow still uses:

`schema/file inspection -> model contract planning -> model contract review -> schema/finaliser validation -> model codegen -> model testgen -> workspace execution -> generated pytest -> deterministic validation -> workflow/system reports -> archive/evidence`.

The DeepSeek benchmark confirmed `model_called` events for planning, schema repair, review, codegen, testgen, and repair where applicable. It also confirmed generated pytest and deterministic validation remain hard gates.

## Scaffold/Fallback Skips

No scaffold/fallback skips model-authored planning/review by default.

The only discovered skip/fallback path is the legacy bank reference scaffold, now explicitly disabled by default with `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD=false`.

## DeepSeek Provider/Config

Implemented/verified:

- `DEEPSEEK_API_KEY` is read from environment/settings and is not logged.
- `DEEPSEEK_BASE_URL` defaults to `https://api.deepseek.com`.
- DeepSeek uses OpenAI-compatible `/chat/completions` requests.
- Stage model routing is environment/config driven:
  - `AUTHOR_PLANNING_MODEL`
  - `AUTHOR_REVIEW_MODEL`
  - `AUTHOR_CODEGEN_MODEL`
  - `AUTHOR_TESTGEN_MODEL`
  - `AUTHOR_REPAIR_MODEL`
- `AUTHOR_MODEL_PROVIDER` is accepted as an alias for `LLM_PROVIDER`.
- Missing key raises a clear `DEEPSEEK_API_KEY is missing` error.
- Timeout and HTTP errors surface as `ModelClientError`; transient 429/5xx/connect/timeout failures honor configured retries.
- `model_called` payloads include provider, model, base URL, purpose, stage, stop reason, token usage, and `duration_ms`.
- Ollama support is preserved for `LLM_PROVIDER=ollama`.

No API key was committed.

## Web Build Blockers

The previously known frontend blockers did not reproduce:

- `cd apps/web && npm run typecheck`: passed.
- `cd apps/web && npm run build`: passed.

## Validation Run Results

- `python -m compileall apps/api/src apps/api/tests -q`: passed.
- Targeted backend suite: 338 tests passed.
- DeepSeek provider suite: 13 tests passed.
- `cd apps/web && npm run typecheck`: passed.
- `cd apps/web && npm run build`: passed.

## Benchmark Summary

Detailed results are in:

- `reports/deepseek_general_author_benchmark.md`
- `reports/deepseek_general_author_benchmark.json`

High-level result:

- Bank reference input completed end-to-end through archive without scaffold.
- Expense exception review failed honestly at generated pytest/deterministic validation and produced failure evidence.
- `invoice_aging_cleanup.csv` was not present, so that run was skipped.
