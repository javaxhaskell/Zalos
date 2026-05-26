# Bank Transaction Categoriser

A starter template for an AgentForge author session. Drops a finance user into a runnable Python agent that categorises bank transactions into six standard categories using a simple rule-based engine.

## What this agent does

Reads a CSV of bank transactions and writes the same rows with two extra columns: `category` (one of six finance categories) and `rule_matched` (a trace string indicating which rule fired). Refund-first override means any row with `amount < 0` is tagged `Refund` regardless of description.

**Categories:** Income · Office Expense · Travel · Subscriptions · Refund · Uncategorised.

## Input shape

| Column | Type | Notes |
|---|---|---|
| `txn_id` | string | Unique per row (e.g., `T-00001`). |
| `date` | ISO `YYYY-MM-DD` | |
| `amount` | decimal | Positive = money out; negative = refund. |
| `description` | string | Bank-supplied description. |
| `counterparty` | string | Parsed counterparty; may be empty. |
| `account` | string | Account identifier. |

## Output shape

Input columns are preserved. Two columns are added:

| Column | Notes |
|---|---|
| `category` | One of `Income · Office Expense · Travel · Subscriptions · Refund · Uncategorised`. |
| `rule_matched` | Trace: `negative_amount`, `subscription_pattern`, `travel_pattern`, `office_pattern`, `income_pattern`, `vendor_map:<name>`, `no_match`. |

## Rules (first match wins)

1. `amount < 0` → `Refund` (refund-first override).
2. Description matches `google|microsoft|aws|github|stripe|notion|figma|slack` → `Subscriptions`.
3. Description matches `uber|lyft|airbnb|train|airline|taxi|cab|bp|shell|esso|petrol|fuel` → `Travel`.
4. Description matches `office|supplies|equipment|amazon|staples|stationery|printer|paper|furniture|whsmith` → `Office Expense`.
5. Description matches `salary|payroll|client payment` → `Income`.
6. Counterparty (case-insensitive) in known-vendor map → mapped category.
7. Otherwise → `Uncategorised`.

## Run

```bash
python agent.py data/sample_input.csv data/output.csv
```

Reads `data/sample_input.csv` (200 synthetic rows) and writes a categorised output. The committed `data/golden_output.csv` is the hand-verified expected output for the sample input — the test suite asserts the agent reproduces it exactly.

## Tests

```bash
pip install -r requirements.txt
pytest tests/ -q
```

Three checks:

- `test_happy_path_matches_golden` — agent's output equals `data/golden_output.csv` row-aligned on `txn_id`.
- `test_refund_first_override` — a negative-amount row whose description matches Subscriptions is still `Refund`.
- `test_output_schema_and_enum_validity` — column set is correct, every row has a category, every category is in the allowed enum, Uncategorised rate < 25%.

## Regenerating the sample

The committed `data/sample_input.csv` is produced deterministically (seeded). To regenerate:

```bash
cd data && python _generate.py
```

If you regenerate the sample, you must also regenerate the golden:

```bash
cd ..
python agent.py data/sample_input.csv data/golden_output.csv
```

## Edge cases this template demonstrates

- Negative amounts (refunds) always win, even with description that would otherwise match Subscriptions.
- Null `counterparty` rows are categorised from `description` alone.
- Case-insensitive vendor matching (`AWS` and `aws` both work).
- Whitespace / punctuation noise in descriptions handled by regex patterns.
- Ambiguous descriptions land in `Uncategorised`; the rate is bounded below 25% by the test.

## Synthetic data only

Data in `data/` is synthetic. Vendor names are made up. No real bank IBANs, real tax IDs, or real client data anywhere in this template.
