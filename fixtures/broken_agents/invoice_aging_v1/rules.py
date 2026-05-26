"""Aging-bucket logic.

Standard 4-bucket aging report:
  0-30  · 31-60  · 61-90  · 90+

Future-dated invoices (negative age) are treated as current (0-30).
"""

from __future__ import annotations


def compute_aging_bucket(age_days: int) -> str:
    """Return the aging bucket for an invoice given its age in days."""
    if age_days < 0:
        return "0-30"
    if age_days <= 30:
        return "0-30"
    if age_days <= 60:
        return "31-60"
    if age_days <= 90:
        return "61-90"
    return "90+"
