# Final Expense Author GREEN Evidence

**Fresh session:** `2bd70711-6fbd-422a-bf70-437b9f27f9e5`  
**Prior failed attempts (diagnostic):**
- `99a92c59-2353-471c-9d41-211b0352b698` — missing `review_required` column; `exception_reason` over-required
- `779f477c-08d1-42d6-be64-3e9dfbf414e5` — all validation tiers PASS but contract hash gate fail (fixed by `sync_author_output_contract_provenance`)
- `05bfe9f9-b1d9-451b-95f9-ec505326a644` — golden 7/7 PASS but generated pytest collected only 1 test
- `ec76b043-5a63-4f99-b193-0ca8f75feb2c` — all tiers PASS but `exception_flag` used `no_issue`/`review_required` instead of canonical `yes`/`no` (UI/demo regression)

**Superseded GREEN sessions:** `98942274-b4fb-4e0d-a3b8-0b8144bc01db`, `e619c679-a832-49d8-9172-93275ff9971c`, `ec76b043-5a63-4f99-b193-0ca8f75feb2c`

**Prompt:** *Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.*  
**Input:** `blind_eval_cases/expense_exception_review/input.csv` (7 rows)  
**Archive:** `.workspaces/2bd70711-6fbd-422a-bf70-437b9f27f9e5/archive.zip`

---

## Run metrics

| Metric | Value |
|--------|-------|
| Status | `completed` |
| Runtime | **275 s (~4.6 min)** |
| Tokens | **64,987** / 150,000 |
| Model stages | contract_planning, contract_review, code_generation, test_generation, execution_repair |
| Model calls | 5 |
| Generated pytest | **4/4 pass** |
| Contract hash gate | **PASS** (backend policy re-hash via `sync_author_output_contract_provenance`) |

---

## Flagged rows (5/7 — matches independent oracle)

| expense_id | exception_flag | review_required | rule_used | notes |
|------------|----------------|-----------------|-----------|-------|
| EXP-001 | yes | yes | amount_over_limit | 120 > 100 limit |
| EXP-002 | yes | yes | missing_receipt | receipt false |
| EXP-003 | no | no | (empty) | clean |
| EXP-004 | yes | yes | approval_not_final | pending |
| EXP-005 | yes | yes | suspicious_notes | personal weekend travel |
| EXP-006 | no | no | (empty) | clean |
| EXP-007 | yes | yes | amount_over_limit; missing_receipt; approval_not_final; suspicious_notes | multi-rule semicolon join |

---

## Four validation tiers

| Tier | Check | Status |
|------|-------|--------|
| Universal | Required deliverables | PASS |
| Universal | Row count 7→7, PK unique | PASS |
| Contract-specific | Schema / required columns / enums / exception consistency | PASS |
| Generated pytest | 4/4 collected tests passed | PASS |
| Golden-output | 7/7 rows matched golden | **PASS** |

**Overall:** PASS

---

## Fix verification

| Fix | Applied | Evidence |
|-----|---------|----------|
| Expense golden policy (`review_required`, `exception_flag` yes/no, optional `exception_reason` on clean rows) | **Yes** | Contract requires `review_required` and `exception_flag`; clean rows have empty `exception_reason` |
| Contract hash re-sync after backend policy | **Yes** | Three `model_authoring_provenance` events; no `author_non_llm_artifact_maker_detected` |
| Post-pytest production output restore | **Yes** | Row count 7→7 after pytest |
| Multi-rule `rule_used` semicolon validation | **Yes** | EXP-007 `rule_used` uses `; ` separator |
| Path leakage sanitisation | **Yes** | User-facing reports use workspace-relative paths |
| Independent golden | **Yes** | `evals/golden/expense_exception_review/expected_output.csv` staged; Check 8 PASS |
| No scaffold / no hardcoded row IDs | **Yes** | 5 model stages; backend infers guidance only |

---

## Path check

- `reports/validation_report.md`: **clean** (workspace-relative paths)
- `reports/system_validation_report.md`: **clean**
- `events.jsonl`: may retain absolute paths (internal audit — acceptable)

---

## Final verdict: **GREEN**

Presentation-ready Expense Exception Review Author demo. All validation tiers PASS including independent golden comparison 7/7; contract hash gate PASS; status `completed`; 5/7 oracle-aligned flagged rows; canonical `exception_flag` yes/no; no user-facing path leakage.

**Demo URL:** `http://localhost:3000/author/2bd70711-6fbd-422a-bf70-437b9f27f9e5`
