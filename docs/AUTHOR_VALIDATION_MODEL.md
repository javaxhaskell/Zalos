# Author Validation Model

AgentForge Author workflows use a four-layer validation and testing architecture. Correctness is **evidence-based**, not claimed as universally perfect.

## Thesis

**The model authors the workflow-specific specification; deterministic validators enforce it.**

The LLM proposes:

- `AuthorOutputContract` — typed output schema, enums, formulas, deliverables, exception rules
- `generated/agent.py` — workflow implementation
- `generated/tests/test_agent.py` — workflow-specific pytest suite

The backend deterministically:

- validates contract structure before codegen
- runs static safety scans
- executes the generated agent in a sandbox
- runs universal checks
- runs contract-specific checks derived from the contract
- runs generated pytest
- optionally compares output against an independent golden CSV

Golden files and expected outputs are **validation oracles only**. They must never be fed into codegen as implementation shortcuts.

## Four layers

### 1. Universal validation

Always run for every Author workflow where applicable:

- required output files and reports exist
- generated agent and tests exist
- output CSV exists
- row count preserved when input is available
- primary row key unique when declared
- static safety scan passes before execution
- archive, manifest, and event log produced at completion

These checks do not encode domain business rules.

### 2. Contract-specific validation

Mechanically derived from the model-authored `AuthorOutputContract`:

- `output_columns` / `required_output_columns`
- `allowed_enums`
- `calculated_fields` and `tolerances`
- `primary_row_key` semantics
- `exception_output_files` consistency with flagged rows
- `aggregation_specs` / summary file coverage
- `requested_deliverables` marked required

The LLM decides workflow content; the backend enforces the contract shape.

### 3. Generated pytest

Model-authored, workflow-specific tests in `generated/tests/test_agent.py`.

These may include:

- universal artifact/runtime smoke checks
- contract-backed column and enum assertions
- behavioural tests traceable to the user prompt, uploaded sample, and contract

They must not invent unsupported rules, require exact report wording unless the contract demands it, or treat golden CSVs as implementation guides.

The backend requires at least three collected tests and a passing run.

### 4. Golden-output comparison (optional)

When an **independent** expected output file is staged (for example `expected_output.csv` supplied separately from codegen):

- compare actual output after execution
- report pass/fail clearly
- skip when no independent golden is available

This layer provides optional sample-level comparison; it is not authoring guidance.

See also [`validation.md`](./validation.md) for tier naming, module map, and test references.

## Failure classification

When validation fails, the system reports the most specific layer:

| Error code | Meaning |
|---|---|
| `universal_validation_failed` | Universal structural check failed |
| `contract_specific_validation_failed` | Contract-derived check failed |
| `generated_pytest_failed` | Model-authored pytest did not pass |
| `golden_output_comparison_failed` | Independent golden comparison failed |
| `safety_validation_failed` | Static safety scan failed |
| `artifact_validation_failed` | Required artifact missing or corrupted |

Legacy codes (`author_validation_failed`, `author_generated_code_failed`) remain for ambiguous or older paths.

## Reports

`reports/system_validation_report.md` groups checks under the four layer headings with plain-language descriptions. The JSON sidecar preserves per-check evidence for automation.

## What this model does not claim

- No guarantee that every possible finance edge case is covered
- No hidden backend presets for custom workflow business logic
- No substitution of golden files for model-authored logic
- Optional auxiliary reference checks provide additional validation evidence only; they do not replace the normal Author path or steer code generation

Correctness is demonstrated through layered, auditable evidence: contract conformance, generated tests, and optional independent golden comparison.
