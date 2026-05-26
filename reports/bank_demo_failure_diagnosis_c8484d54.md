# Bank Demo Failure Diagnosis: c8484d54-4c2e-4861-8494-06b948952b0e

## Summary

The bundled bank reference sample uploaded successfully and reached model-authored contract planning, contract review, code generation, test generation, test-generation JSON repair, and two execution repair attempts.

The real failure was generated-agent execution, not the reference-sample route and not deterministic validation.

## Real Failure

- Session ID: `c8484d54-4c2e-4861-8494-06b948952b0e`
- Furthest stage reached: execution repair attempt 2
- Failing stage: `pre_author_validation_gate`
- Error code: `author_generated_code_failed`
- Generated pytest result: not reached
- Deterministic validation result: not reached
- Generated agent execution result: exited `1`

The final stack trace was:

```text
TypeError: string indices must be integers, not 'str'
File ".../generated/agent.py", line 74, in main
    output_columns = [col['name'] for col in contract['output_columns']]
```

The validated contract stores `output_columns` and `required_output_columns` as lists of strings:

```json
"output_columns": ["category", "rule_matched", "rule_used", "confidence_score", "review_required"],
"required_output_columns": ["category", "rule_matched", "rule_used", "confidence_score", "review_required"]
```

The generated agent incorrectly treated those lists as lists of objects with `name` fields.

## Outputs

Produced outputs:

- none under `outputs/`
- `reports/model_authoring_summary.md`

Missing required outputs:

- `outputs/output.csv`
- `reports/validation_report.md`

## Repair Attempts

Execution repair ran twice. Both repair candidates still contained the same invalid contract shape access pattern:

```python
output_columns = [col['name'] for col in contract['output_columns']]
required_columns = set(col['name'] for col in contract['required_output_columns'])
```

So repair applied but did not fix the root cause.

## UI Error Issue

The full `technical_detail` contained the real stack trace. The short `workflow_failed.message` was built from the last 800 characters of the detail. Because the detail ended with `workspace_artifact_tree`, the UI showed only artifact paths instead of the exception.

## Files Inspected

- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/events.jsonl`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/manifest.json`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/author_output_contract.json`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_contract_plan.json`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_contract_review.json`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/agent.py`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/tests/test_agent.py`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/repairs/attempt_1/agent.py`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/repairs/attempt_2/agent.py`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_responses/execution_repair_1.prompt.txt`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_responses/execution_repair_1.txt`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_responses/execution_repair_2.prompt.txt`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_responses/execution_repair_2.txt`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_responses/generated__agent.py.patch.json`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/generated/model_responses/generated__tests__test_agent.py.patch.json`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/outputs/`
- `.workspaces/c8484d54-4c2e-4861-8494-06b948952b0e/reports/`
