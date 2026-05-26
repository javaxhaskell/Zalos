# Requirements coverage

This document maps each assignment requirement to the exact implementation in this repository. Use it as an implementation checklist — every row links to load-bearing code or docs, not aspirational copy.

## Assignment requirements

| Requirement | Implementation | How to verify |
|---|---|---|
| **UI for both workflows** | Dashboard [`apps/web/app/page.tsx`](../apps/web/app/page.tsx) + [`WorkflowPicker`](../apps/web/src/components/workflow-picker.tsx); Author wizard [`apps/web/app/author/[sid]/page.tsx`](../apps/web/app/author/[sid]/page.tsx); Repair wizard [`apps/web/app/repair/[sid]/page.tsx`](../apps/web/app/repair/[sid]/page.tsx) | Open `/`, walk Author + Repair end-to-end |
| **Open-source foundation** | Bounded agent loop [`apps/api/src/agentforge/agent/loop.py`](../apps/api/src/agentforge/agent/loop.py); attribution in [`README.md`](../README.md), [`ARCHITECTURE.md`](../ARCHITECTURE.md) §4, [`docs/adr/0001-open-source-foundation.md`](./adr/0001-open-source-foundation.md) | Read ADR-0001; no vendored OpenHands code |
| **File-enabled workspace** | [`WorkspaceManager`](../apps/api/src/agentforge/persistence/workspace.py) allocates `.workspaces/<session-id>/`; path pinning via `resolve_in()` | `GET /sessions/{id}` returns `workspace_path`; inspect disk |
| **Tool calls (inspect / edit / run / test / validate / summarise)** | Typed registry [`apps/api/src/agentforge/tools/`](../apps/api/src/agentforge/tools/); 20 tools listed in [`CONTRACTS.md`](../CONTRACTS.md) §4 | `test_tool_registry.py`; audit log `tool_invoked` events |
| **Synthetic samples** | Synthetic CSV/XLSX samples, including [`templates/bank_categoriser/data/sample_input.csv`](../templates/bank_categoriser/data/sample_input.csv), are available as uploads for LLM-first Author runs | Upload the sample and verify the model authors contract/code/tests |
| **Broken-agent sample for repair** | [`fixtures/broken_agents/invoice_aging_v2/`](../fixtures/broken_agents/invoice_aging_v2/) (boundary bug — recommended demo); `invoice_aging_v1` retained for date-format history | Repair wizard → load `invoice_aging_v2`; 2 failed / 5 passed before fix |
| **Tests / golden checks** | Author: model-generated pytest/checks plus contract-driven validation; Repair: before/after pytest + `repair_report` | Author `validation_report.md`; Repair pytest decision_input events |
| **Setup + run instructions** | [`README.md`](../README.md) Prerequisites + Setup + Run; [`docs/LOCAL_WALKTHROUGH.md`](./LOCAL_WALKTHROUGH.md) | Fresh clone: `make setup && make migrate && make up` |
| **Transcript / build narrative** | [`TRANSCRIPT.md`](../TRANSCRIPT.md) | Read curated build story |
| **Resume / return later** | SQLite session row + `events.jsonl` + `manifest.json` + workspace tree; [`ResumeBanner`](../apps/web/src/components/resume-banner.tsx) | Reload `/author/{id}` or `/repair/{id}` after leaving |

## Evidence-gated completion (not fake success)

| Workflow | Completion path | Gate location |
|---|---|---|
| **Author** | `completion_via=ai_authored_workflow_build` | [`execute_custom_workflow_pipeline`](../apps/api/src/agentforge/orchestrator/author_custom_build.py) + [`enforce_author_completion_gate`](../apps/api/src/agentforge/orchestrator/author_llm_authoring.py) — requires model-authored contract plan, model-reviewed contract, model-authored `generated/agent.py`, model-authored tests/checks, execution evidence, validation report, provenance artifacts, and archive |
| **Repair** (`invoice_aging_v2`) | `completion_via=repair_validated_patch` | [`RepairFlow._execute_repair_pipeline`](../apps/api/src/agentforge/orchestrator/repair_flow.py) — requires before-fix failure, validated patch, after-fix all-green, repair reports |

Neither workflow emits `workflow_completed` without passing its evidence gate. Author cannot complete with zero model calls or non-model final artifacts. The UI derives “complete” from artifacts + events, not from `session.status` alone.

## Model provider

| Item | Location |
|---|---|
| Ollama default | `.env.example` → `LLM_PROVIDER=ollama`; [`OllamaModelClient`](../apps/api/src/agentforge/models/) |
| Provider metadata in audit | `model_called` events; `manifest.completion.llm_provider` / `llm_model` / `llm_base_url` |
| Health check | `GET /health/ready` verifies DB + Ollama + model pull |

## Deliverables map

| Deliverable | Path |
|---|---|
| Validation report (Author) | `reports/validation_report.md` + JSON sidecar |
| Repair report (Repair) | `reports/repair_report.md` + `reports/repair_report.json` |
| Manifest completion metadata | `manifest.json` → `completion` block [`CompletionMetadata`](../apps/api/src/agentforge/schemas/session.py) |
| Append-only audit | `events.jsonl` [`EventLog`](../apps/api/src/agentforge/persistence/event_log.py) |
| Downloadable archive | `archive.zip` via [`build_archive`](../apps/api/src/agentforge/persistence/archive.py) + `GET /sessions/{id}/archive.zip` |
| Session README in archive | `SESSION_README.md` (Author vs Repair specific) |
| Eval harness | [`evals/`](../evals/) + `POST /evals/run` + `/admin/evals` |
| About page | [`apps/web/app/about/page.tsx`](../apps/web/app/about/page.tsx) |

## Automated regression

```bash
make test-api          # full backend suite
make test-template     # bank_categoriser reference-sample tests
make eval              # 3 eval scenarios
cd apps/web && pnpm exec tsc --noEmit
```

Key tests locking happy paths:

- [`tests/test_author_flow_e2e.py`](../apps/api/tests/test_author_flow_e2e.py) — LLM-first Author completion, required model stages, no spurious `workflow_failed`
- [`tests/test_author_completion_gate.py`](../apps/api/tests/test_author_completion_gate.py) — zero-token guard, contract review guard, model JSON/schema repair, missing file repair, safety repair, and non-LLM artifact tamper detection
- [`tests/test_repair_evidence_gate.py`](../apps/api/tests/test_repair_evidence_gate.py) — `repair_validated_patch`, manifest completion, repair artifacts

## Intentional exclusions

See [`docs/adr/0010-scope-exclusions.md`](./adr/0010-scope-exclusions.md) — no real bank integration, no production auth, subprocess sandbox only, no Playwright in CI.
