"""Payment processor reconciliation rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_ISSUE_NOTE_KEYWORDS = ("chargeback", "dispute", "refund")
_FAILED_STATUS_VALUES = frozenset(
    {"failed", "declined", "chargeback", "dispute", "refund", "reversed", "rejected"}
)
_REVIEW_STATUS_VALUES = frozenset({"pending", "processing", "in_review", "on_hold"})
_SUCCESSFUL_STATUS_VALUES = frozenset(
    {"settled", "successful", "success", "completed", "paid", "cleared"}
)


def _to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _pick_field(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def gross_amount(row: dict[str, str]) -> Decimal | None:
    return _to_decimal(_pick_field(row, "gross_amount", "gross"))


def fee_amount(row: dict[str, str]) -> Decimal | None:
    return _to_decimal(_pick_field(row, "fee_amount", "fee"))


def net_amount(row: dict[str, str]) -> Decimal | None:
    return _to_decimal(_pick_field(row, "net_amount", "net"))


def reconcile_row(row: dict[str, str]) -> tuple[str, str, str, str, str, str, float]:
    """Return calculated fields for one settlement row."""
    gross = gross_amount(row)
    fee = fee_amount(row)
    reported_net = net_amount(row)

    calculated_net = None
    if gross is not None and fee is not None:
        calculated_net = gross - fee

    difference = None
    if calculated_net is not None and reported_net is not None:
        difference = calculated_net - reported_net

    status_value = (_pick_field(row, "status") or "").lower()
    notes = (_pick_field(row, "notes") or "").lower()
    customer_reference = _pick_field(row, "customer_reference", "customer_ref")

    issue_reasons: list[str] = []
    if gross is None or fee is None or reported_net is None:
        issue_reasons.append("missing_amount_fields")
    if difference is not None and abs(difference) > Decimal("0.01"):
        issue_reasons.append("net_amount_mismatch")
    if "status" in row and status_value in _FAILED_STATUS_VALUES:
        issue_reasons.append("processor_status")
    if "status" in row and status_value in _REVIEW_STATUS_VALUES:
        issue_reasons.append("pending_status")
    if "notes" in row and any(keyword in notes for keyword in _ISSUE_NOTE_KEYWORDS):
        issue_reasons.append("notes_keyword")
    if (
        ("customer_reference" in row or "customer_ref" in row)
        and not customer_reference
    ):
        issue_reasons.append("missing_customer_reference")

    if gross is None or fee is None or reported_net is None:
        status = "needs_review"
        rule_used = "missing_amount_fields"
        confidence = 0.0
    elif difference is not None and abs(difference) <= Decimal("0.01") and not issue_reasons:
        status = "matched"
        rule_used = "net_amount_matches_gross_minus_fee"
        confidence = 1.0
    elif "net_amount_mismatch" in issue_reasons:
        status = "investigate"
        rule_used = "net_amount_mismatch"
        confidence = 0.4
    elif issue_reasons:
        status = "needs_review"
        rule_used = issue_reasons[0]
        confidence = 0.5
    elif (
        "status" in row
        and status_value
        and status_value not in _SUCCESSFUL_STATUS_VALUES
    ):
        status = "needs_review"
        rule_used = "non_success_processor_status"
        confidence = 0.6
        issue_reasons.append("non_success_processor_status")
    else:
        status = "matched"
        rule_used = "net_amount_matches_gross_minus_fee"
        confidence = 1.0

    issue_flag = "yes" if issue_reasons or status != "matched" else "no"

    calc_str = "" if calculated_net is None else f"{calculated_net:.2f}"
    expected_str = "" if reported_net is None else f"{reported_net:.2f}"
    diff_str = "" if difference is None else f"{difference:.2f}"
    return calc_str, expected_str, diff_str, status, issue_flag, rule_used, confidence


def summarize_by_group(
    rows: list[dict[str, str]],
    group_key: str,
) -> list[dict[str, str]]:
    """Aggregate payment reconciliation rows by ``group_key``."""
    from decimal import Decimal

    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = (row.get(group_key) or "").strip() or "(empty)"
        groups.setdefault(key, []).append(row)

    summary_rows: list[dict[str, str]] = []
    for key, group_rows in sorted(groups.items()):
        gross_total = Decimal("0")
        fee_total = Decimal("0")
        net_total = Decimal("0")
        calc_total = Decimal("0")
        issue_count = 0
        for row in group_rows:
            gross = gross_amount(row)
            fee = fee_amount(row)
            net = net_amount(row)
            calc = _to_decimal(row.get("calculated_net_amount"))
            if gross is not None:
                gross_total += gross
            if fee is not None:
                fee_total += fee
            if net is not None:
                net_total += net
            if calc is not None:
                calc_total += calc
            if (row.get("issue_flag") or "").strip().lower() == "yes":
                issue_count += 1
        summary_rows.append(
            {
                group_key: key,
                "transaction_count": str(len(group_rows)),
                "gross_total": f"{gross_total:.2f}",
                "fee_total": f"{fee_total:.2f}",
                "net_total": f"{net_total:.2f}",
                "calculated_net_total": f"{calc_total:.2f}",
                "issue_count": str(issue_count),
            }
        )
    return summary_rows


def exception_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return rows flagged for review."""
    return [row for row in rows if (row.get("issue_flag") or "").strip().lower() == "yes"]
