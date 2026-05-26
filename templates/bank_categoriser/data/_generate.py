"""Generate sample_input.csv deterministically.

Run from this directory:

    cd templates/bank_categoriser/data && python _generate.py

Produces ``sample_input.csv`` with 200 rows distributed across the six
canonical categories with deliberate edge cases (refunds, null counterparty,
ambiguous descriptions). Uses ``random.seed(42)`` so output is stable.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta

random.seed(42)

# (counterparty | None, description, amount_base)
SUBSCRIPTIONS = [
    ("Google Workspace", "GOOGLE WORKSPACE SUBSCRIPTION", 14.99),
    ("Microsoft 365", "MICROSOFT 365 BUSINESS PLAN", 12.50),
    ("AWS", "AWS MONTHLY USAGE CHARGES", 145.00),
    ("GitHub", "GITHUB PRO SUBSCRIPTION", 4.00),
    ("Stripe", "STRIPE PROCESSING FEE", 25.00),
    ("Notion", "NOTION WORKSPACE TEAM", 8.00),
    ("Figma", "FIGMA PROFESSIONAL PLAN", 12.00),
    ("Slack", "SLACK PREMIUM WORKSPACE", 7.25),
]

OFFICE = [
    ("Amazon", "AMAZON ORDER OFFICE SUPPLIES", 45.00),
    ("Amazon", "AMAZON UK PRINTER PAPER", 22.50),
    ("Staples", "STAPLES OFFICE PURCHASE", 31.99),
    ("WHSmith", "WHSMITH STATIONERY ORDER", 18.00),
    ("Dell", "DELL EQUIPMENT PURCHASE", 1200.00),
    ("IKEA", "IKEA OFFICE FURNITURE DELIVERY", 350.00),
    (None, "OFFICE SUPPLIES ROUTINE", 50.00),
    (None, "EQUIPMENT MAINTENANCE", 120.00),
]

TRAVEL = [
    ("Uber", "UBER TRIP LDN-CITY", 18.40),
    ("Uber", "UBER EATS BUSINESS LUNCH", 24.00),
    ("Lyft", "LYFT RIDE TO MEETING", 22.00),
    ("Trainline", "TRAIN BOOKING LDN-MAN", 89.50),
    ("BA", "BRITISH AIRLINE LHR-MAD", 450.00),
    (None, "BLACK CAB CARDIFF CITY", 35.50),
    ("BP", "BP FUEL STATION M25", 65.00),
    ("Shell", "SHELL PETROL FORECOURT", 72.00),
    ("Esso", "ESSO FUEL FORECOURT", 58.00),
]

INCOME = [
    ("Client Inc", "CLIENT PAYMENT INVOICE 101", 5000.00),
    ("ABC Ltd", "CLIENT PAYMENT INV-2026-014", 3200.00),
    ("Payroll", "SALARY PAYMENT MONTHLY", 4500.00),
]

UNCATEGORISED = [
    (None, "UNKNOWN VENDOR XYZ", 14.99),
    ("Misc Co", "MISC PURCHASE NO DETAILS", 22.00),
    (None, "TRANSACTION REF 4521", 88.00),
    ("Anon Ltd", "ANONYMOUS TRANSFER", 200.00),
    (None, "REF 9982142 NO DESC", 12.00),
    ("Generic", "GENERIC PAYMENT", 150.00),
]

# Refunds (negative amounts) — descriptions may or may not match other rules;
# the refund-first override ensures Refund regardless.
REFUNDS = [
    ("Amazon", "REFUND AMAZON ORDER", -45.00),
    ("Uber", "UBER REFUND OVERCHARGE", -18.40),
    (None, "REFUND FROM VENDOR", -50.00),
    ("AWS", "AWS REFUND CREDIT", -145.00),  # description matches Subscriptions, but Refund wins
]


def random_date(start: date, end: date) -> date:
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, delta_days))


def fuzz(amount: float, variance: float = 0.12) -> float:
    return round(amount * (1 + random.uniform(-variance, variance)), 2)


PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 3, 31)

# Plan: 200 rows split across the six categories with realistic mix.
PLAN: list[tuple[list, int]] = [
    (SUBSCRIPTIONS, 45),
    (OFFICE, 55),
    (TRAVEL, 35),
    (REFUNDS, 12),
    (INCOME, 8),
    (UNCATEGORISED, 45),
]

rows: list[dict[str, str]] = []
for source, count in PLAN:
    for _ in range(count):
        counterparty, description, amount_base = random.choice(source)
        rows.append(
            {
                "date": random_date(PERIOD_START, PERIOD_END).isoformat(),
                "amount": f"{fuzz(amount_base):.2f}",
                "description": description,
                "counterparty": counterparty if counterparty else "",
                "account": "ACC-001" if random.random() > 0.15 else "ACC-002",
            }
        )

# Shuffle then assign stable txn_ids in shuffled order
random.shuffle(rows)
for i, row in enumerate(rows, start=1):
    row["txn_id"] = f"T-{i:05d}"

# Reorder dict to canonical column order
FIELDNAMES = ["txn_id", "date", "amount", "description", "counterparty", "account"]
rows = [{k: r[k] for k in FIELDNAMES} for r in rows]

with open("sample_input.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} rows to sample_input.csv")
