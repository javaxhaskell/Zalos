# Repair Agent Sample Diagnosis — `invoice_aging_v2`

**Date:** 2026-05-25  
**Sample:** Invoice aging boundary repair (built-in)

---

## 1. Actual vs expected behaviour

| Aspect | Expected (correct agent) | Actual (broken fixture) |
|---|---|---|
| 31-day-overdue bucket | `31-60` | `1-30` |
| 31-day-overdue risk flag | `medium` | `low` |
| Affected invoices | INV-0005, INV-0013, INV-0018 | Same three misbucketed |
| Pytest before repair | 7 passed, 0 failed | **5 passed, 2 failed** |
| Pytest after canonical fix | 7 passed, 0 failed | 7 passed, 0 failed |

---

## 2. Evidence table

| # | Check | Result | Evidence |
|---|---|---|---|
| E1 | Fixture pytest without patch | **2 failed, 5 passed** | `pytest tests/ -v` in `fixtures/broken_agents/invoice_aging_v2` |
| E2 | Failing test names | `test_boundary_31_days_in_31_60_bucket`, `test_output_matches_expected_output_csv` | pytest stdout |
| E3 | Root cause line | `if days_overdue <= 31:` at `agent.py:71` | source inspection |
| E4 | UI copy match | "2 of 7 tests fail", "one-line boundary bug" | `apps/web/app/repair/[sid]/page.tsx` `BUILTIN_SAMPLE_AGENT.description` |
| E5 | Problem report match | Three 31-day invoices in wrong bucket | `problem_report.md` |
| E6 | HTTP load_fixture | 200, `working/agent.py` copied, golden staged | `test_repair_run_endpoint.py::test_run_drives_invoice_aging_v2_to_completed_through_http` |
| E7 | Before-fix pytest (pipeline) | failed=2, passed=5 | `decision_input` `pytest_before_fix` |
| E8 | Patch applied | `<= 30` present, `<= 31` absent | `working/agent.py` after run |
| E9 | After-fix pytest | failed=0, passed=7 | `decision_input` `pytest_after_fix` |
| E10 | Repair report | `reports/repair_report.md` + `.json` with 8 sections | evidence gate test + HTTP test |
| E11 | Terminal status | `completed` | session GET |
| E12 | Runtime (HTTP E2E) | **~2.3 s** (FakeModelClient, no real API) | pytest timing |
| E13 | Archive | `archive.zip` + `SESSION_README.md` | `build_archive()` in evidence gate test |

---

## 3. Root-cause classification

**Category:** Off-by-one boundary error in pure classification logic  
**File:** `fixtures/broken_agents/invoice_aging_v2/agent.py`  
**Function:** `categorise()`  
**Bug:** Upper bound for the `1-30` bucket uses `days_overdue <= 31` instead of `<= 30`.  
**Impact:** Invoices exactly 31 days overdue satisfy the first overdue branch and receive bucket `1-30` and risk `low` instead of `31-60` / `medium`.  
**Not:** Date parsing, schema, row count, paid/current logic, or high-risk threshold — those paths pass independent tests.

---

## 4. UI copy match

| UI claim | Verified |
|---|---|
| "Invoice aging boundary repair" | Yes — workflow picker + repair page title |
| "one-line boundary bug" | Yes — single comparison operator change |
| "2 of 7 tests fail" | Yes — exact pytest split |
| "invoices exactly 31 days overdue land in the wrong bucket" | Yes — INV-0005/13/18 |
| Reproduce → fix → re-validate | Yes — deterministic pipeline |

Minor doc drift: fixture `README.md` says "6 tests; 2 fail" but `test_agent.py` defines **7** tests. UI and tests agree on 7.

---

## 5. Gaps for submission

| Gap | Severity | Notes |
|---|---|---|
| Eval R-01 still targets v1 date bug | Low | v2 is the demo path; v1 remains for eval harness |
| Real LLM path latency untested in this run | Low | Inference fallback completes demo without API |
| Frontend `npm run build` ESLint | Medium | Unrelated unused-vars in author preview components block build |
| `invoice_aging_v1` vs `v2` naming in older docs | Low | README mentions both; wizard uses v2 |

**No fake-success path observed:** workflow fails honestly when evidence gates fail (`test_repair_evidence_gate.py`).

---

## 6. Minimal fix plan

Repair workflow is **already implemented** and tests pass. Remaining work:

1. **No fixture/orchestrator change required** — boundary bug and inference path are correct.
2. **Fix frontend ESLint** (3 unused-var warnings) so Phase 4 `npm run build` passes — scoped to author preview components, not repair logic.
3. **Optional:** Align `invoice_aging_v2/README.md` test count (6 → 7) for doc consistency.

---

## 7. Canonical patch behaviour

```diff
-    if days_overdue <= 31:
+    if days_overdue <= 30:
         return ("1-30", "low")
```

Additional orchestrator cleanup replaces deliberate-bug fixture comments with repaired-boundary comments (see `repair_proposal.py` `_STALE_*` patterns).

---

## 8. Post-repair validation

- All 7 pytest tests pass
- Output matches `data/expected_output.csv` row-aligned on `invoice_id`
- `repair_report.json` records `primary_problem.source = built_in_sample_problem_report`
- Manifest `completion.completion_via = repair_validated_patch`
