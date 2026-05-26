# Expense Exception Review Author — Semantic Gap Diagnosis

**Session:** `4a9a4343-a56f-4557-886b-855999a19160`  
**Workspace:** `.workspaces/4a9a4343-a56f-4557-886b-855999a19160`  
**Prompt:** *Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.*  
**Run summary:** completed · 210.5 s · 45,328 tokens · 3/3 pytest · four-tier validation **PASS** · golden **SKIPPED** · presentation **AMBER**

---

## Seven diagnostic questions

### 1. Contract — what rules did the model author?

The planned contract (`generated/author_output_contract.json`) defines **two** exception rules only:

| Rule | Condition | Severity |
|------|-----------|----------|
| `amount_exceeded_*` | `amount > policy_limit` | high |
| `missing_receipt_*` | `receipt_attached == 'false'` | medium |

**Missing from contract:** `approval_not_final` (pending/rejected), `suspicious_notes` (personal/duplicate/cash advance). No `review_required`, `severity`, `rule_used`, or `confidence` output columns. `golden_comparison_requirement: skipped`.

**Root cause:** Contract planning/review guidance did not infer standard expense-schema rules from columns (`amount`, `policy_limit`, `receipt_attached`, `approval_status`, `notes`). Model authored a minimal subset; backend did not override.

---

### 2. Codegen — what did the agent implement?

`generated/agent.py` implements exactly the two contract rules in `compute_exception()`:

- Amount over policy limit
- Receipt in `{false, no, 0}`

No approval-status check. No notes keyword scan. Report writer echoes raw `args.input` and `args.row_output` (lines 190–191), which become absolute paths when pytest passes absolute CLI paths.

**Root cause:** Codegen faithfully followed the thin contract; report path guidance was insufficient for absolute CLI args.

---

### 3. Tests — what did testgen produce?

`generated/tests/test_agent.py` — **3 tests**, all artifact/structure checks:

1. Required artifacts exist  
2. Row count + `exception_flag` enum  
3. Exceptions CSV header columns  

No behavioural tests for pending approval, suspicious notes, or multi-rule rows. Tests invoke the agent with **absolute** `--input` / `--row-output` paths (`str(INPUT_CSV)`), re-writing `validation_report.md` with machine paths after backend sanitisation.

**Root cause:** Testgen guidance lacked expense-specific behavioural requirements and did not forbid absolute CLI paths strongly enough.

---

### 4. Validation — why did deterministic validation PASS at 3/7 flagged?

| Check | Result | Notes |
|-------|--------|-------|
| Row count 7→7 | PASS | All rows preserved |
| Required `exception_flag` non-null | PASS | Every row has flag |
| Enum `no_issue` / `policy_exception` | PASS | Consistent with contract |
| Exception list consistency | PASS | 3 flagged = 3 exception rows |
| Generated pytest | PASS | 3/3 |
| Golden | SKIPPED | No independent golden staged |

Validation is **contract-driven and honest** — it passed because agent output matched the **authored** contract, not because it matched full finance intent on the 7-row sample.

**Independent oracle** (`blind_eval_cases/expense_exception_review/oracle.py`) expects **5** flagged rows: EXP-001, EXP-002, EXP-004 (pending), EXP-005 (personal notes), EXP-007. Session flagged **3**: EXP-001, EXP-002, EXP-007.

---

### 5. Rows — per-row outcome vs expected finance rules

| expense_id | amount/limit | receipt | approval | notes signal | Session flagged | Oracle expects |
|------------|--------------|---------|----------|--------------|-----------------|----------------|
| EXP-001 | 120 > 100 | true | approved | — | **yes** | yes |
| EXP-002 | 150 ≤ 200 | false | approved | missing receipt text | **yes** | yes |
| EXP-003 | ok | true | approved | — | no | no |
| EXP-004 | ok | true | **pending** | — | **no** | **yes** |
| EXP-005 | ok | true | approved | **personal** | **no** | **yes** |
| EXP-006 | ok | true | approved | — | no | no |
| EXP-007 | 500 > 400 | false | rejected | duplicate claim | **yes** | yes |

**Gap:** EXP-004 and EXP-005 are false negatives relative to prompt-implied finance review, but true negatives relative to the model-authored contract.

---

### 6. Path leakage — where and why?

`reports/validation_report.md` lines 3–4:

```
/Users/arhamshuaib/Desktop/Zalos/.workspaces/4a9a4343-…/uploads/input.csv
/Users/arhamshuaib/Desktop/Zalos/.workspaces/4a9a4343-…/outputs/output.csv
```

`reports/system_validation_report.md` uses relative paths only.

**Root cause:** Backend sanitised reports **before** pytest; generated tests re-ran the agent with absolute CLI paths and overwrote the sanitised report. Codegen also echoed raw argparse paths.

---

### 7. Golden — why SKIPPED and is it safe to add?

Check 8 SKIPPED with honest evidence: no independent `expected_output.csv` staged; codegen did not read golden files.

**Safe to add:** Hand-verified golden on stable columns (`expense_id`, `review_required`) for the committed 7-row blind-eval sample only. Stage at validation time; never inject into codegen prompts or agent-readable paths.

---

## Root cause summary

| Issue | Class | General fix |
|-------|-------|-------------|
| 3/7 flagged (oracle 5/7) | Contract planning gap | Expense-schema rule inference guidance |
| Path leakage in agent report | Sanitise timing + codegen/testgen | Post-pytest sanitisation; relative CLI paths |
| Golden SKIPPED | No staged oracle | Independent `expected_output.csv` for expense sample |
| AMBER verdict | Combined above | Fixes target future sessions; do not edit archived session |

---

## Fix targets (this pass)

- `author_llm_authoring.py` — expense schema guidance (planning/review/codegen/testgen); golden staging helpers  
- `author_custom_build.py` — post-pytest sanitisation; expense golden staging  
- `user_facing.py` — always sanitise `reports/validation_report.md`  
- `evals/golden/expense_exception_review/expected_output.csv` — hand-verified oracle alignment  
- Tests in `test_final_demo_output_quality.py`

**Do not change:** Session `4a9a4343` archive, validation gates, row-count hardcoding, or model-authored contract in the archived workspace.
