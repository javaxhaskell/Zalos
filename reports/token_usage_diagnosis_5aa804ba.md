# Token Usage Diagnosis: 5aa804ba-0272-4c65-84fd-cfc94c56bfdc

## Session summary

| Field | Value |
| --- | --- |
| Session ID | `5aa804ba-0272-4c65-84fd-cfc94c56bfdc` |
| Input | `bank_transaction_categorisation_demo.csv` (18 rows) |
| Terminal status | `failed_other` / `author_generated_code_failed` |
| Failed layer | `generated_pytest` (2 failed / 6 passed) |
| Budget | **113,726 / 150,000** tokens (~76%) |
| Wall time | ~725 s |

Related pytest-failure diagnosis: [reports/bank_generated_pytest_failure_diagnosis_5aa804ba.md](./bank_generated_pytest_failure_diagnosis_5aa804ba.md)

---

## Per-model-call breakdown

| Stage | Model | Input | Output | Total | Duration | Prompt chars | Response chars | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| contract_planning | deepseek-v4-pro | 8,212 | 9,119 | 17,331 | 153.4s | 37,333 | 11,618 | Full planning prompt + large contract JSON output |
| contract_schema_repair | deepseek-v4-pro | 11,866 | 5,690 | 17,556 | 91.0s | 53,923 | 11,733 | Repeated schema guidance + invalid contract payload |
| contract_review | deepseek-v4-pro | 13,855 | 8,290 | 22,145 | 137.6s | 60,940 | 13,113 | Indented JSON prompt; full rules duplicated; full planned contract |
| code_generation | deepseek-v4-flash | 5,450 | 4,933 | 10,383 | 38.2s | 24,877 | 18,239 | Already compact contract in prompt |
| test_generation | deepseek-v4-flash | 6,619 | 2,789 | 9,408 | 21.7s | 29,826 | 10,278 | Compact contract; long test-quality checklist |
| execution_repair #1 | deepseek-v4-flash | 13,789 | 4,904 | 18,693 | 39.0s | 54,806 | 10,589 | **Hit max_tokens=4096** (near cap) |
| execution_repair #2 | deepseek-v4-flash | 14,819 | 3,391 | 18,210 | 24.0s | 58,196 | 10,041 | Repeated full repair prompt; test-only scope |

**Total model-call tokens:** 113,726

---

## Aggregates

| Metric | Value |
| --- | --- |
| Total tokens | 113,726 |
| Top 3 stages by tokens | contract_review (22,145), execution_repair #1 (18,693), contract_schema_repair (17,556) |
| Contract stages subtotal | 57,032 (50%) |
| Codegen + testgen subtotal | 19,791 (17%) |
| Repair loop subtotal | 36,903 (32%) |
| Repair attempts | 2 × ~18.5k ≈ 37k |

---

## Token waste classification (A–E)

| Class | Description | Evidence in this session | Est. waste |
| --- | --- | --- | --- |
| **A — Repeated schema/guidance** | `_contract_prompt_rules()` (~10k chars) and full guidance blocks sent on planning, schema repair, and review | Review prompt 60,940 chars; schema repair 53,923 chars; rules alone ~10k chars each stage | ~25–35k input tokens across contract stages |
| **B — Verbose prompt encoding** | Review/repair prompts used `json.dumps(..., indent=2)` vs compact `_json_prompt` | Review 60,940 vs ~50,459 compact JSON (~17% formatting overhead) | ~2–4k tokens |
| **C — Large contract re-sent** | Full `author_output_contract` in review + both repair prompts | Full dump 12,902 chars vs compact 9,626 chars | ~3–5k tokens per stage |
| **D — Repair loop amplification** | Two execution repairs with ~55k-char prompts; attempt 1 hit output cap | 36,903 repair tokens; pytest routed to test-only repair (ineffective) | ~18k tokens (second attempt) |
| **E — Large model outputs (contract stages)** | Pro model returns full AuthorOutputContract JSON repeatedly | Planning out 9,119; review out 8,290; schema repair out 5,690 | Inherent cost; mitigated by flash review on small workflows |

---

## Repeated schema/guidance analysis

1. **`_contract_prompt_rules()`** appears in planning, schema repair, and review (~10,075 chars each).
2. **Guidance objects** (`validation_check_guidance`, `output_column_semantics_guidance`, etc.) duplicated across all three contract stages.
3. **`exact_schema_example`** sent on planning and schema repair; unnecessary on review when planned contract is present.
4. **Repair prompts** embed full contract, full output semantics, full pytest repair checklist (~3,329 chars), and 6,000-char failure detail even for two short assertion failures.

---

## Implemented general optimisations (post-diagnosis)

| Fix | Change |
| --- | --- |
| **A — Prompt compression** | `_contract_stage_guidance(mode=...)` with core rules on review/schema_repair; omit `exact_schema_example` on review |
| **B — Deduplication** | `_contract_prompt_rules_core()` replaces full rules on non-planning stages |
| **C — Compact schema** | `_contract_for_prompt(..., compact=True)` in review/repair for small workflows |
| **D — Repair excerpting** | `_compact_repair_failure_detail`, `_compact_repair_artifact_context`, `_compact_output_contract_summary`, `_compact_pytest_repair_requirements` |
| **E — Repair loop guard** | Default `author_max_repair_attempts=1`; skip repeat repair when failure signature unchanged |
| **F — Model routing** | Small workflows use flash model override for contract review |
| **G — Product mode encoding** | Review/execution repair prompts use `_json_prompt` (compact JSON) |
| **H — Instrumentation** | `_record_stage_prompt_metrics` emits `author_prompt_metrics` events per stage |

New settings: `AUTHOR_MAX_REPAIR_ATTEMPTS`, `AUTHOR_SMALL_WORKFLOW_ROW_THRESHOLD`, `AUTHOR_COMPACT_CONTRACT_PROMPTS`.

---

## Estimated impact (18-row bank workflow)

| Area | Before | Estimated after | Savings |
| --- | ---: | ---: | ---: |
| Review prompt chars | 60,940 | ~42,000 | ~19k chars |
| Repair prompt chars (×1 attempt) | 54,806 | ~38,000 | ~17k chars |
| Repair loop | 2 attempts | 1 default | ~18k tokens |
| **Total session tokens** | **113,726** | **~55–70k** | **~40–50%** |

Exact live savings verified by fresh DeepSeek rerun (Phase 6).

---

## Phase 6 — Fresh DeepSeek rerun (after optimisations)

| Field | Before (5aa804ba) | After (1939f0a5) |
| --- | ---: | ---: |
| Session ID | `5aa804ba-0272-4c65-84fd-cfc94c56bfdc` | `1939f0a5-c9cf-4bc0-aa10-640717abe401` |
| Input rows | 18 | 18 |
| Terminal status | failed (`generated_pytest`) | **completed** |
| Total tokens | 113,726 | **44,788** (−61%) |
| Model calls | 7 | 4 |
| Repair attempts | 2 | 0 |

### Tokens by stage (after)

| Stage | Model | Input | Output | Total |
| --- | --- | ---: | ---: | ---: |
| contract_planning | deepseek-v4-pro | 7,954 | 5,851 | 13,805 |
| contract_review | deepseek-v4-pro | 7,412 | 6,099 | 13,511 |
| code_generation | deepseek-v4-flash | 4,547 | 3,860 | 8,407 |
| test_generation | deepseek-v4-flash | 5,412 | 3,653 | 9,065 |

### Prompt metrics (after)

| Stage | Prompt chars | Compact mode |
| --- | ---: | --- |
| contract_planning | 36,418 | false |
| contract_review | 33,285 | true |

Workspace: `.workspaces/1939f0a5-c9cf-4bc0-aa10-640717abe401`

---

## Artifact paths inspected

- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/events.jsonl`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/model_responses/*.prompt.txt`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/generated/debug/codegen_prompt_meta.json`
- `.workspaces/5aa804ba-0272-4c65-84fd-cfc94c56bfdc/manifest.json`
- `/Users/arhamshuaib/Downloads/agentforge-session-5aa804ba-0272-4c65-84fd-cfc94c56bfdc.zip` (present; workspace artifacts used)
