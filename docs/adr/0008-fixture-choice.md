# ADR-0008 — Fixture choice

**Status:** accepted
**Date:** 2026-05-21

## Context

The assignment requires at least one broken existing-agent fixture and synthetic samples for authoring. The fixtures define what the demo actually shows, so the choice is product-load-bearing.

## Decision

**Author fixture: Bank transaction categorisation.**

- Input: `bank_transactions.csv`, 200 synthetic GBP rows over 3 months.
- Columns: `txn_id`, `date`, `amount` (positive=outflow, negative=refund), `description`, `counterparty` (5% null), `account`.
- Output: input columns + `category` (enum) + `rule_matched` (trace).
- Categories: Income, Office Expense, Travel, Subscriptions, Refund, Uncategorised.
- Rules: negative amounts → `Refund`; description-pattern → category; counterparty in vendor-map → category; otherwise → `Uncategorised`.
- Golden output: hand-verified `golden_output.csv` (200 rows) committed under `evals/golden/bank_categoriser/`.

**Repair fixture: Invoice aging date-format bug.**

- Bundled at `fixtures/broken_agents/invoice_aging_v1/`.
- 30 synthetic invoices in `data/sample_input.csv` with dates in `MM-DD-YYYY` format.
- `agent.py:18` parses with `datetime.strptime(row["invoice_date"], "%d-%m-%Y")` — assumes DD-MM-YYYY (the bug).
- 3 pytest tests; one (`test_april_invoices_in_first_bucket`) fails on a clean clone because April invoices either raise `ValueError` (e.g., `04-15-2026` has no day 15 in DD-MM) or silently misparse (e.g., `04-03-2026` becomes 3rd April instead of 4th March).
- Fix is a one-line change to use `dateutil.parser.parse(..., dayfirst=False)` or accept a `date_format` parameter.
- Expected golden output: hand-verified `data/expected_output.csv` (correct aging buckets).

## Options considered

### Author

| Candidate | Pros | Cons | Pick |
|---|---|---|---|
| **Bank transaction categorisation** | Single CSV, deterministic, clean golden, refund edge case is universally recognised | Domain looks simple — counter with rigorous validation | **Selected** |
| Vendor payment preparation | Realistic, multi-system feel | Multi-table, busy UI, golden harder to author | Not selected |
| Invoice aging cleanup | Touches dates (prompt-engineering surface) | Overlaps repair fixture's bug domain | Not selected |
| Expense exception review | Multi-rule, threshold-based | Risk of clarifying-question explosion | Not selected |

### Repair

| Candidate | Pros | Cons | Pick |
|---|---|---|---|
| **Invoice aging date bug** | Dramatic visible bug; one-line fix; universally relatable | None material | **Selected** |
| Expense categoriser case-sensitivity | High reproducibility; one-line `.lower()` fix | Less dramatic | Strong fallback |
| Revenue normalisation missing FX | High reproducibility | Requires rates table — more setup | Not selected |
| Payment reconciliation float tolerance | High reproducibility | Less domain-relatable; more technical | Not selected |

## Rationale

- The two fixtures span different domains (categorisation vs aging), demonstrating breadth without overlap.
- Both have hand-verifiable golden outputs of modest size (200 rows / 30 rows).
- The bug in the repair fixture is dramatic ("April invoices show as 90+ days") and the fix is small (one line), making the repair flow demo-friendly.
- The author fixture exercises every category, the refund-override rule, and the null-counterparty edge case — a small but exhaustive demo.

## Consequences

- The `bank_categoriser` template under `templates/` ships with `agent.py`, `rules.py`, `tests/test_agent.py`, `requirements.txt`, and `data/`.
- The `invoice_aging_v1` fixture ships with the deliberate bug at `agent.py:18`, three pytest tests (with one failing), and the expected-output golden.
- Eval scenarios A-01 (author) and R-01 (repair) target these fixtures exactly.
- Adversarial scenario ADV-01 uses a synthetic copy of the bank-categoriser sample with a prompt-injection payload in one row's `description` field.

## Reversal condition

- Add a second author fixture (e.g., expense exception review) when bandwidth allows; document as ADR-001x.
- Add a second repair fixture if a different bug archetype (case-sensitivity, float tolerance) demonstrates better in the demo.
