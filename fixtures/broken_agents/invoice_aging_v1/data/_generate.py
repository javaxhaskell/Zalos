"""Generate sample_input.csv with 30 invoices in MM-DD-YYYY format.

Run from this directory:

    cd fixtures/broken_agents/invoice_aging_v1/data && python _generate.py

Distribution: 8 April, 10 March, 7 February, 5 January 2026 invoices.
The April rows deliberately include days both ≤12 (which silently misparse
under DD-MM-YYYY interpretation) and >12 (which raise ValueError). This is
what makes the date-format bug observable as a failing test.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta

random.seed(42)

VENDORS = ["ACME Ltd", "Globex Corp", "Initech Services", "Umbrella Pharma", "Acme Engineering"]

# (start, end, count) per month
PLAN = [
    (date(2026, 4, 1), date(2026, 4, 30), 8),
    (date(2026, 3, 1), date(2026, 3, 31), 10),
    (date(2026, 2, 1), date(2026, 2, 28), 7),
    (date(2026, 1, 1), date(2026, 1, 31), 5),
]

rows: list[dict[str, str]] = []
counter = 1

for start, end, count in PLAN:
    delta = (end - start).days
    days_in_window: list[date] = []
    # Ensure April mix includes both a day≤12 and a day>12 to expose both
    # failure modes of the date-format bug.
    if start.month == 4:
        days_in_window = [
            date(2026, 4, 3),   # ≤12: silently misparses (DD-MM read: 4th March)
            date(2026, 4, 8),   # ≤12: silently misparses
            date(2026, 4, 12),  # ≤12: silently misparses
            date(2026, 4, 15),  # >12: raises ValueError under DD-MM
            date(2026, 4, 20),  # >12: raises ValueError
            date(2026, 4, 22),  # >12: raises ValueError
            date(2026, 4, 27),  # >12: raises ValueError
            date(2026, 4, 29),  # >12: raises ValueError
        ]
        assert len(days_in_window) == count
    else:
        # Fill with random days in the month range
        used: set[date] = set()
        while len(days_in_window) < count:
            d = start + timedelta(days=random.randint(0, delta))
            if d not in used:
                used.add(d)
                days_in_window.append(d)
        days_in_window.sort()

    for d in days_in_window:
        rows.append(
            {
                "invoice_id": f"INV-{d.isoformat()}-{counter:03d}",
                "invoice_date": d.strftime("%m-%d-%Y"),  # MM-DD-YYYY
                "vendor": random.choice(VENDORS),
                "amount": f"{random.uniform(500, 12000):.2f}",
            }
        )
        counter += 1

# Shuffle for realistic mixed order
random.shuffle(rows)

with open("sample_input.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["invoice_id", "invoice_date", "vendor", "amount"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} invoices to sample_input.csv")
