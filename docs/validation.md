# Validation architecture

Author workflows use a **four-tier** deterministic validation stack. Repair workflows use **six evidence pieces** (reproduction, patch, post-fix test, report). This doc names the Author tiers; full detail is in [`AUTHOR_VALIDATION_MODEL.md`](./AUTHOR_VALIDATION_MODEL.md).

## Author four tiers

| Tier | Name | Source | Fails as |
|---|---|---|---|
| 1 | Universal | Platform checks (files exist, row count, safety scan) | `universal_validation_failed` |
| 2 | Contract-specific | Derived from `AuthorOutputContract` | `contract_specific_validation_failed` |
| 3 | Generated pytest | `generated/tests/test_agent.py` | `generated_pytest_failed` |
| 4 | Golden output (optional) | Independent `expected_output.csv` | `golden_output_comparison_failed` |

Implementation modules:

- `validation/layers.py` — layer runners
- `validation/golden.py` — primary key resolution, row diff
- `orchestrator/author_contract_validation.py` — contract-backed checks
- `orchestrator/author_custom_build.py` — orchestrates tier execution after agent run

### Tier ordering

Tiers run after sandbox execution. Safety scan runs **before** execution (not counted as a tier but gates runtime). Golden tier is skipped when no independent oracle is staged.

### What tiers do not do

- Golden files are never injected into codegen prompts (validation oracles only).
- Generated pytest must assert contract-backed semantics, not incidental report wording.
- Universal tier does not encode domain business rules.

## Repair evidence (contrast)

Repair validation is evidence-gated, not layer-stacked:

1. Fixture loaded
2. Failure reproduced
3. Patch validated and applied
4. Post-fix pytest pass
5. Repair report generated
6. Archive packaged

See [`repair-workflow.md`](./repair-workflow.md).

## Reports

- Author: `reports/system_validation_report.md` + JSON sidecar
- Repair: `reports/repair_report.md`

User-facing text is sanitized in `persistence/user_facing.py` (workspace-relative paths only).

## Enum and formula checks

Contract tier enforces `allowed_enums`, calculated fields with tolerances, exception file consistency, and deliverable coverage. Multi-value enum fields (semicolon-joined) are validated token-wise when the contract allows it.

## Tests

| File | Coverage |
|---|---|
| `test_validation_layers.py` | Per-layer happy/failure paths |
| `test_author_validation_architecture.py` | Tier integration |
| `test_author_custom_workflow_gate.py` | End-to-end gate behaviour |
| `test_author_completion_gate.py` | Contract repair bounds, completion gates |

Run focused suite:

```bash
cd apps/api && python -m pytest tests/test_validation_layers.py -q
```
