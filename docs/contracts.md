# Contracts

Canonical Pydantic schemas, event kinds, tool registry shape, and workspace paths: [`CONTRACTS.md`](../CONTRACTS.md).

## Contract-first Author flow

```
User description + upload profile
        ↓
AuthorOutputContract (model-authored, strict JSON)
        ↓ validate before codegen
generated/agent.py + generated/tests/test_agent.py
        ↓
Execution + four-tier validation against contract
```

The backend never generates business logic from templates alone. Templates and reference samples are prompt context only.

## Key schemas

| Schema | Location | Boundary |
|---|---|---|
| `AuthorOutputContract` | `schemas/author_output_contract.py` | Model → backend validation |
| `AuthorRequirements` | `schemas/` | API create/run requests |
| `WorkspaceEvent` | `schemas/event.py` | Event log lines |
| `ResumeManifest` | `schemas/session.py` | Workspace manifest.json |
| `ValidationReport` | `schemas/` | Tier-grouped check results |
| `RepairReport` | `schemas/` | Six-section repair output |

All API and tool boundaries use Pydantic v2 strict mode with `extra="forbid"` (INV-8).

## Contract validation before codegen

`author_llm_authoring.py` enforces:

1. JSON parse of model contract response
2. `_validate_contract_payload_strict` — semantic rules, reserved deliverable paths
3. Bounded schema repair on validation failure (one re-prompt)
4. Contract review stage before code generation

Invalid contracts cannot reach `code_generation`. Tests: `test_author_completion_gate.py`, `test_author_output_contract_schema.py`.

## Backend-managed paths

The contract must not declare user deliverables that target backend-managed files:

- `events.jsonl`, `manifest.json`, `archive.zip`, `SESSION_README.md`
- `generated/author_output_contract.json`
- `reports/model_authoring_summary.md`

Declared writes to these paths fail semantic validation. Generated agent code is static-scanned for the same paths. See [`sandbox-and-artifacts.md`](./sandbox-and-artifacts.md).

## Provenance hash gate

At Author completion, `generated/author_output_contract.json` hash must match the post-execution synced contract (`sync_author_output_contract_provenance`). Execution may adjust contract fields to reflect actual outputs; the sync step rewrites the persisted contract before the final gate.

## TypeScript mirrors

Generated from OpenAPI via `packages/shared-schemas`. Run `make gen-schemas` after API schema changes.

## Versioning

Additive schema changes within a build are allowed with ADR note. Breaking renames require a new ADR and migration plan per [`CONTRACTS.md` §9](../CONTRACTS.md).
