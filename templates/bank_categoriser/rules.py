"""Category rules for bank transaction categorisation.

Rule order (first match wins):
  1. negative_amount        → Refund
  2. subscription_pattern   → Subscriptions
  3. travel_pattern         → Travel
  4. office_pattern         → Office Expense
  5. income_pattern         → Income
  6. vendor_map             → mapped category (case-insensitive)
  7. fallback               → Uncategorised
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "Income",
    "Office Expense",
    "Travel",
    "Subscriptions",
    "Refund",
    "Uncategorised",
)

# Description regex patterns (case-insensitive). Anchored on whole-word
# boundaries where it matters; ``bp ``/``shell`` etc are intentional.
SUBSCRIPTION_RE = re.compile(
    r"google|microsoft|aws|github|stripe|notion|figma|slack",
    re.IGNORECASE,
)
TRAVEL_RE = re.compile(
    r"uber|lyft|airbnb|train|airline|taxi|cab|\bbp\b|shell|esso|petrol|fuel",
    re.IGNORECASE,
)
OFFICE_RE = re.compile(
    r"office|supplies|equipment|amazon|staples|stationery|printer|paper|furniture|whsmith",
    re.IGNORECASE,
)
INCOME_RE = re.compile(
    r"salary|payroll|client payment",
    re.IGNORECASE,
)

# Counterparty → category mapping. Counterparty is matched lower-cased
# against the keys; first hit wins.
VENDOR_MAP: Mapping[str, str] = {
    "google workspace": "Subscriptions",
    "microsoft 365": "Subscriptions",
    "aws": "Subscriptions",
    "uber": "Travel",
    "lyft": "Travel",
    "amazon": "Office Expense",
    "staples": "Office Expense",
    "dell": "Office Expense",
    "client inc": "Income",
}


def categorise_row(row: Mapping[str, str]) -> tuple[str, str, float]:
    """Return ``(category, rule_matched, confidence)`` for a single row.

    Confidence heuristic (calibrated for finance-user signal):

      * ``1.00`` — refund override fired on a negative amount.
      * ``0.95`` — a description regex matched.
      * ``0.70`` — vendor-map hit only.
      * ``0.50`` — amount was unparseable; categorisation is best-effort.
      * ``0.00`` — no rule matched; row is Uncategorised.

    Inputs are tolerant of CSV-native strings; ``amount`` is parsed as float.
    """
    raw_amount = row.get("amount", "0")
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        return ("Uncategorised", "unparseable_amount", 0.50)

    description = (row.get("description") or "").strip()
    counterparty = (row.get("counterparty") or "").strip()

    if amount < 0:
        return ("Refund", "negative_amount", 1.00)

    if SUBSCRIPTION_RE.search(description):
        return ("Subscriptions", "subscription_pattern", 0.95)
    if TRAVEL_RE.search(description):
        return ("Travel", "travel_pattern", 0.95)
    if OFFICE_RE.search(description):
        return ("Office Expense", "office_pattern", 0.95)
    if INCOME_RE.search(description):
        return ("Income", "income_pattern", 0.95)

    if counterparty:
        mapped = VENDOR_MAP.get(counterparty.lower())
        if mapped:
            return (mapped, f"vendor_map:{counterparty.lower()}", 0.70)

    return ("Uncategorised", "no_match", 0.00)
