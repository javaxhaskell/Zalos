# Invoice Aging v1 — broken-agent fixture

A small Python finance agent that produces an aging report from a CSV of invoices. **It ships with a known issue**, deliberately, as the demo target for AgentForge's repair workflow.

## What the agent is supposed to do

Read `data/sample_input.csv` (30 invoices) and write an aging report. For each invoice, compute `age_days = today - invoice_date` (today defaults to **2026-04-30**) and bucket into one of `0-30`, `31-60`, `61-90`, `90+`.

```bash
python agent.py data/sample_input.csv outputs/aged.csv
```

## What's wrong with it

The clerk reports (see `data/problem_report.md`):

> "Our April invoices showed up in the 90+ days bucket instead of 0-30. The March ones are showing 60-90 days. Numbers look wrong across the board. Eight or so April invoices didn't make it into the output at all — their bucket reads `PARSE_ERROR`."

This is real. Run `pytest tests/test_aging.py -q` from this directory and you'll see 2 passing tests and 1 failing test (`test_april_invoices_in_first_bucket`), with diagnostic evidence in the failure message.

## What an AgentForge repair session will do

The repair workflow's job is to reproduce, diagnose, propose a fix, apply it (after approval), re-validate, and write a report. We do not pre-disclose the fix here — that's what the session produces. The fixture is sealed against direct patches; the repair workflow's `RepairReport` is the deliverable.

## Folder structure

```
invoice_aging_v1/
├── README.md                 (this file)
├── requirements.txt          (pytest only — pure stdlib otherwise)
├── agent.py                  (entry point; has the issue)
├── rules.py                  (aging-bucket logic; correct)
├── data/
│   ├── sample_input.csv      (30 invoices, MM-DD-YYYY format)
│   ├── expected_output.csv   (what a correct agent would produce)
│   ├── problem_report.md     (the clerk's report)
│   ├── _generate.py          (deterministic sample generator; seed=42)
│   └── _expected.py          (correct-output generator, for reference)
└── tests/
    ├── conftest.py
    └── test_aging.py         (3 tests: 2 pass + 1 fail on a clean clone)
```

## Acceptance criteria for this fixture

On a clean clone (no fix applied yet):

```bash
cd fixtures/broken_agents/invoice_aging_v1
pip install -r requirements.txt
pytest tests/ -q
# Expected: 2 passed, 1 failed
```

After AgentForge's repair workflow lands a patch:

- All 3 tests pass.
- Running `python agent.py data/sample_input.csv /tmp/aged.csv` produces output equal to `data/expected_output.csv` on the `aging_bucket` column.

## Regenerating the data

The committed CSVs are produced deterministically:

```bash
cd data
python _generate.py    # writes sample_input.csv (30 rows, seed=42)
python _expected.py    # writes expected_output.csv (correct buckets)
```

The April rows in the generator are deliberately chosen (3 with day ≤ 12 and 5 with day > 12) to demonstrate both failure modes of the underlying issue.

## Synthetic data only

Vendor names and invoice IDs are made up. No real customer or vendor data anywhere in this fixture.
