# Invoice Aging v2 — broken-agent fixture

A small, self-contained Python finance agent that produces an aging
report from a CSV of invoices. **It ships with a deliberate boundary
bug** that the AgentForge Repair workflow can be tested against.

## Business purpose

For each invoice, compute `days_overdue = today - due_date` (today is
fixed at **2026-05-01** for deterministic batch reporting) and
classify it into:

| Bucket | Condition |
|---|---|
| `Paid` | `status == "paid"` |
| `Current` | `days_overdue < 0` |
| `1-30` | `1 ≤ days_overdue ≤ 30` |
| `31-60` | `31 ≤ days_overdue ≤ 60` |
| `61-90` | `61 ≤ days_overdue ≤ 90` |
| `90+` | `days_overdue > 90` |

Risk flags:

| Flag | Condition |
|---|---|
| `none` | Paid or Current |
| `low` | 1-30 |
| `medium` | 31-60, 61-90, or 90+ below high-risk threshold |
| `high` | 90+ **AND** `amount ≥ 1000` |

## Schema

Input columns: `invoice_id, customer, invoice_date, due_date, amount, status`.

Output columns: input + `days_overdue, aging_bucket, risk_flag`.

## The bug (intentional)

In `agent.py` the rule cascade reads:

```python
if days_overdue <= 31:
    return ("1-30", "low")
```

The upper bound should be `<= 30`. The off-by-one routes every
invoice that is exactly 31 days overdue into the `1-30` bucket
instead of `31-60`. The bundled sample has **three** such invoices
(`INV-0005`, `INV-0013`, `INV-0018`) so the failure is reproducible.

## Folder structure

```
invoice_aging_v2/
├── README.md
├── problem_report.md
├── requirements.txt
├── agent.py                                (the broken agent)
├── data/
│   ├── input_invoices.csv                  (20 synthetic rows)
│   └── expected_output.csv                 (the correct output)
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_agent.py                       (7 tests; 2 fail on the bug)
```

## Reproduce the failure

```bash
cd fixtures/broken_agents/invoice_aging_v2
python3 -m pytest tests/ -q
# Expected: 5 passed, 2 failed
#   FAILED tests/test_agent.py::test_boundary_31_days_in_31_60_bucket
#   FAILED tests/test_agent.py::test_output_matches_expected_output_csv
```

## Repair through AgentForge

Start a **Repair session** from the dashboard, pick **Invoice aging v2
(boundary bug)** in the fixture picker, paste (or upload) the
`problem_report.md`, and click **Start agent**. The repair workflow:

1. Copies the fixture into the session workspace.
2. Inspects the files and dependencies.
3. Reproduces the failure by running pytest.
4. Identifies the boundary condition in `agent.py`.
5. Proposes a one-line patch (`<= 31` → `<= 30`).
6. Applies the patch after approval.
7. Re-runs the tests (5 → 7 pass).
8. Generates `repair_report.md` with files-changed, before/after test
   evidence, and remaining risks.

## Synthetic data only

All vendor names, invoice IDs, amounts, and dates are made up. No
real customer, vendor, or banking data anywhere in this fixture.
