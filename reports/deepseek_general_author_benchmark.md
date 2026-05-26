# DeepSeek General Author Benchmark

Date: 2026-05-25

Mode: normal LLM-first Author path. `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD=false`.

Stage routing used:

| Stage | Model |
|---|---|
| contract_planning | deepseek-v4-pro |
| contract_schema_repair | deepseek-v4-pro |
| contract_review | deepseek-v4-pro |
| code_generation | deepseek-v4-flash |
| test_generation | deepseek-v4-flash |
| execution_repair | deepseek-v4-flash |

| Workflow | Session ID | Provider/models | Wall time | Status | Furthest stage | Outputs | Reports | Archive | Failure |
|---|---|---:|---:|---|---|---|---|---|---|
| bank_transaction_categorisation_demo | 94c58f84-ee35-4c87-a1a2-ebb6e6fa6c6a | DeepSeek: pro planning/schema repair/review; flash codegen/testgen | 340.04s | completed | archive | outputs/output.csv | model_authoring_summary.md; system_validation_report.json/md; validation_report.md | yes | none |
| expense_exception_review | fafd3422-a717-465b-b656-cc382892771f | DeepSeek: pro planning/schema repair/review; flash codegen/testgen/repair | 485.97s | failed_other | archive failure evidence | outputs/output.csv; outputs/exceptions.csv; outputs/manifest.json; outputs/events.jsonl; outputs/archive.zip | SESSION_README.md; model_authoring_summary.md; system_validation_report.json/md; validation_report.md | yes | generated pytest failed: 7 collected, 0 passed; deterministic validation failed exception list consistency, `exception count 5 != flagged rows 0` |
| invoice_aging_cleanup | n/a | n/a | n/a | skipped | n/a | n/a | n/a | n/a | `invoice_aging_cleanup.csv` not found in repository |

## Per-Workflow Notes

### bank_transaction_categorisation_demo

- Planning passed: yes.
- Review passed: yes.
- Codegen ran: yes.
- Generated tests produced: yes.
- Generated pytest collected/passed: 8 collected, passed.
- Execution passed: yes.
- Deterministic validation passed: yes.
- Archive produced: yes.
- Model call durations:
  - contract_planning, deepseek-v4-pro: 104429 ms.
  - contract_schema_repair, deepseek-v4-pro: 61969 ms.
  - contract_review, deepseek-v4-pro: 120417 ms.
  - code_generation, deepseek-v4-flash: 25617 ms.
  - test_generation, deepseek-v4-flash: 26914 ms.

### expense_exception_review

- Planning passed: yes.
- Review passed: yes.
- Codegen ran: yes.
- Generated tests produced: yes.
- Generated pytest collected/passed: 7 collected, failed.
- Execution passed: yes.
- Deterministic validation passed: no.
- Archive/evidence produced: yes, failure archive generated.
- Exact failure:
  - `Generated pytest did not pass any collected tests`.
  - Generated tests invoked `generated/agent.py` from pytest temporary directories where the agent file was absent.
  - Deterministic validation reported `Exception list consistency (row_level): exception count 5 != flagged rows 0`.
  - Repair attempts produced pytest candidates with the same path-family failure.
- Model call durations:
  - contract_planning, deepseek-v4-pro: 141674 ms.
  - contract_schema_repair, deepseek-v4-pro: 86953 ms.
  - contract_review, deepseek-v4-pro: 95610 ms.
  - code_generation, deepseek-v4-flash: 53427 ms.
  - test_generation, deepseek-v4-flash: 39511 ms.
  - execution_repair attempt 1, deepseek-v4-flash: 33199 ms.
  - execution_repair attempt 2, deepseek-v4-flash: 34327 ms.

## Observations

- DeepSeek worked through the OpenAI-compatible provider and emitted provider/model/duration telemetry for every `model_called` event.
- The strong model is still slow for the contract stages: contract planning/review/schema repair dominated wall time.
- The normal Author path can complete end-to-end on the bank reference input without the bank scaffold.
- The expense exception run exposed a general test-generation/pathing failure and a deterministic validation mismatch. The backend correctly failed the run instead of marking it successful.
