"""Six validation layers from ADR-0007.

Each function takes the inputs its layer needs and returns one
:class:`ValidationCheck`. The validate_output tool composes these into
the final :class:`ValidationReport`.

The layers in order:

  1. ``layer_schema`` — output file exists; required columns present.
  2. ``layer_required_columns`` — required columns non-null on every row.
  3. ``layer_business_rules`` — domain invariants from
     :class:`AuthorRequirements.business_rules`. BP5c ships a small
     built-in evaluator (allowed-enum + non-empty); custom predicates
     land with the orchestrator in BP6+.
  4. ``layer_row_level`` — pairwise invariants (row count preserved
     between input and output).
  5. ``layer_golden_output`` — row-aligned comparison against the
     committed golden CSV (uses :mod:`agentforge.validation.golden`).
  6. ``layer_generated_pytest`` — the agent's own pytest run passed.

Layers may be ``SKIPPED`` (passed=True, evidence noting the skip) when
their inputs aren't available — e.g., no golden CSV staged.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path

from agentforge.schemas import (
    BusinessRule,
    TestResults,
    ValidationCheck,
    ValidationLayer,
)
from agentforge.validation.golden import GoldenDiffError, golden_diff, resolve_golden_primary_key

# ---------------------------------------------------------------------------
# Layer 1 — schema
# ---------------------------------------------------------------------------


def layer_schema(
    *,
    actual_csv: Path,
    expected_columns: Iterable[str],
) -> ValidationCheck:
    if not actual_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.SCHEMA,
            name="Output schema",
            passed=False,
            evidence=f"output file {actual_csv.name!r} not found",
            hint_if_failed="ensure the agent wrote to the configured output path",
        )
    columns = _read_header(actual_csv)
    missing = [c for c in expected_columns if c not in columns]
    if missing:
        return ValidationCheck(
            layer=ValidationLayer.SCHEMA,
            name="Output schema",
            passed=False,
            evidence=f"missing columns: {missing!r}",
            detail=f"actual columns: {columns}",
            hint_if_failed="add the missing columns to the agent's output writer",
        )
    return ValidationCheck(
        layer=ValidationLayer.SCHEMA,
        name="Output schema",
        passed=True,
        evidence=f"{len(columns)} columns present, all expected columns matched",
    )


# ---------------------------------------------------------------------------
# Layer 2 — required columns non-null
# ---------------------------------------------------------------------------


def layer_required_columns(
    *,
    actual_csv: Path,
    required_columns: Iterable[str],
) -> ValidationCheck:
    if not actual_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.REQUIRED_COLUMNS,
            name="Required columns non-null",
            passed=False,
            evidence=f"output file {actual_csv.name!r} not found",
        )
    rows = _read_csv(actual_csv)
    if not rows:
        return ValidationCheck(
            layer=ValidationLayer.REQUIRED_COLUMNS,
            name="Required columns non-null",
            passed=False,
            evidence="output has zero rows",
        )
    null_counts: dict[str, int] = dict.fromkeys(required_columns, 0)
    for row in rows:
        for col in null_counts:
            if not row.get(col):
                null_counts[col] += 1
    offenders = {c: n for c, n in null_counts.items() if n > 0}
    if offenders:
        return ValidationCheck(
            layer=ValidationLayer.REQUIRED_COLUMNS,
            name="Required columns non-null",
            passed=False,
            evidence=f"null cells: {offenders!r}",
            hint_if_failed="ensure the agent fills every required column for every row",
        )
    return ValidationCheck(
        layer=ValidationLayer.REQUIRED_COLUMNS,
        name="Required columns non-null",
        passed=True,
        evidence=f"all {len(rows)} rows non-null for {list(null_counts)}",
    )


# ---------------------------------------------------------------------------
# Layer 3 — business rules
# ---------------------------------------------------------------------------


def layer_business_rules(
    *,
    actual_csv: Path,
    rules: Iterable[BusinessRule],
    allowed_enums: Mapping[str, Iterable[str]] | None = None,
) -> ValidationCheck:
    """Domain invariants. BP5c evaluator:

      * Iterates ``allowed_enums`` mapping ``column → set(values)`` and
        flags any row whose value is outside the set.
      * Reports the ``rules`` list back verbatim as the ``detail`` so
        the user sees what was supposed to hold; full predicate
        evaluation lands when :class:`AuthorRequirements` carries
        executable predicates (BP6+).
    """
    if not actual_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Business rules",
            passed=False,
            evidence=f"output file {actual_csv.name!r} not found",
        )

    rules_list = list(rules)
    rule_summary = "; ".join(r.name for r in rules_list) if rules_list else "(none declared)"

    if not allowed_enums:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Business rules",
            passed=True,
            evidence=f"no enum-shaped rules to assert; declared: {rule_summary}",
            detail="BP5c evaluator runs allowed-enum checks only; richer "
            "predicate evaluation lands in BP6+ when AuthorRequirements "
            "carries executable rules.",
        )

    rows = _read_csv(actual_csv)
    violations: list[str] = []
    for col, allowed in allowed_enums.items():
        allowed_set = set(allowed)
        for row in rows:
            value = row.get(col)
            if value is None or value == "":
                continue
            text = str(value).strip()
            if col == "rule_used" and ";" in text:
                tokens = [part.strip() for part in text.split(";") if part.strip()]
                lowered_allowed = {item.lower() for item in allowed_set}
                if not all(token.lower() in lowered_allowed for token in tokens):
                    violations.append(
                        f"row pk={_pk(row)} {col}={value!r} ∉ {sorted(allowed_set)}"
                    )
            elif text not in allowed_set:
                violations.append(f"row pk={_pk(row)} {col}={value!r} ∉ {sorted(allowed_set)}")
            if len(violations) >= 20:
                break
        if len(violations) >= 20:
            break

    if violations:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Business rules",
            passed=False,
            evidence=f"{len(violations)} enum violation(s)",
            detail="\n".join(violations),
            hint_if_failed="tighten the agent's rule cascade or expand the allowed enum",
        )
    return ValidationCheck(
        layer=ValidationLayer.BUSINESS_RULES,
        name="Business rules",
        passed=True,
        evidence=f"enum checks passed across {len(rows)} rows; declared: {rule_summary}",
    )


# ---------------------------------------------------------------------------
# Layer 4 — row-level invariants
# ---------------------------------------------------------------------------


def layer_row_level(
    *,
    actual_csv: Path,
    input_csv: Path | None,
    primary_key: str | None = None,
) -> ValidationCheck:
    """For BP5c: row-count preservation between input and output.

    More invariants land in BP6/eval (sum-of-amounts, primary-key
    uniqueness, etc.).
    """
    if not actual_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.ROW_LEVEL,
            name="Row-level invariants",
            passed=False,
            evidence=f"output file {actual_csv.name!r} not found",
        )
    actual_rows = _read_csv(actual_csv)
    if input_csv is None or not input_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.ROW_LEVEL,
            name="Row-level invariants",
            passed=True,
            evidence=f"input CSV not provided; row count check skipped — "
            f"output has {len(actual_rows)} rows",
        )
    input_rows = _read_csv(input_csv)
    if len(actual_rows) != len(input_rows):
        return ValidationCheck(
            layer=ValidationLayer.ROW_LEVEL,
            name="Row-level invariants",
            passed=False,
            evidence=(
                f"row count drift: input={len(input_rows)}, "
                f"output={len(actual_rows)}"
            ),
            hint_if_failed="ensure the agent emits one output row per input row",
        )
    pieces = [f"row count preserved ({len(input_rows)} → {len(actual_rows)})"]
    if primary_key is not None:
        actual_pks = [r.get(primary_key) for r in actual_rows]
        unique_pks = set(actual_pks)
        if len(unique_pks) != len(actual_pks):
            return ValidationCheck(
                layer=ValidationLayer.ROW_LEVEL,
                name="Row-level invariants",
                passed=False,
                evidence=(
                    f"primary key {primary_key!r} not unique "
                    f"({len(actual_pks) - len(unique_pks)} duplicate(s))"
                ),
                hint_if_failed="ensure the agent does not duplicate input rows",
            )
        pieces.append(f"primary key {primary_key!r} unique")
    return ValidationCheck(
        layer=ValidationLayer.ROW_LEVEL,
        name="Row-level invariants",
        passed=True,
        evidence="; ".join(pieces),
    )


# ---------------------------------------------------------------------------
# Layer 5 — golden output
# ---------------------------------------------------------------------------


def layer_golden_output(
    *,
    actual_csv: Path,
    golden_csv: Path | None,
    primary_key: str | None = None,
    contract_primary_row_key: str | None = None,
    golden_config_primary_key: str | None = None,
    ignore_columns: tuple[str, ...] = (),
) -> ValidationCheck:
    """Compare against an INDEPENDENT golden CSV.

    Returns SKIPPED when no golden is staged. Callers that staged a
    template-generated reference should NOT pass it here — use
    :func:`layer_template_reference_parity` so the audit report is
    honest about what is and isn't independent validation.

    When ``primary_key`` is omitted, :func:`resolve_golden_primary_key`
    selects the alignment column from the contract, golden config, or
    shared ID-like headers. There is no silent ``row_id`` fallback.
    """
    if golden_csv is None or not golden_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.GOLDEN_OUTPUT,
            name="Golden output comparison",
            passed=None,
            skipped=True,
            evidence=(
                "no independent golden CSV staged; this check requires "
                "an externally-supplied expected_output.csv, not one "
                "generated by the same template/agent under test"
            ),
        )
    try:
        resolved_primary_key = primary_key or resolve_golden_primary_key(
            actual_csv=actual_csv,
            golden_csv=golden_csv,
            contract_primary_row_key=contract_primary_row_key,
            golden_config_primary_key=golden_config_primary_key,
        )
    except GoldenDiffError as exc:
        return ValidationCheck(
            layer=ValidationLayer.GOLDEN_OUTPUT,
            name="Golden output comparison",
            passed=False,
            evidence=str(exc),
            detail=str(exc),
            hint_if_failed=(
                "Ensure the agent output and golden expected output share a "
                "stable row identifier column such as expense_id."
            ),
        )
    diff = golden_diff(
        actual_csv=actual_csv,
        golden_csv=golden_csv,
        primary_key=resolved_primary_key,
        ignore_columns=ignore_columns,
    )
    if diff.matched:
        return ValidationCheck(
            layer=ValidationLayer.GOLDEN_OUTPUT,
            name="Golden output comparison",
            passed=True,
            evidence=diff.summary(),
        )
    return ValidationCheck(
        layer=ValidationLayer.GOLDEN_OUTPUT,
        name="Golden output comparison",
        passed=False,
        evidence=f"{diff.total_divergences} divergence(s) vs golden",
        detail=diff.summary(limit=20),
        hint_if_failed="review the agent's rules cascade against the rows that drifted",
    )


def layer_template_reference_parity(
    *,
    actual_csv: Path,
    reference_csv: Path | None,
    primary_key: str,
    template_name: str,
    compare_columns: list[str] | None = None,
) -> ValidationCheck:
    """Compare against the SEEDED TEMPLATE'S reference output.

    This is a parity / smoke check, NOT independent validation: the
    template's reference was itself generated by the same template
    code shipped in this session, so a match only proves the agent
    code wasn't corrupted between seed and execution.

    ``compare_columns`` restricts the diff so that post-processed
    columns (``rule_used``, ``confidence``, etc.) added after the
    template ran don't cause spurious divergence.
    """
    if reference_csv is None or not reference_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.TEMPLATE_REFERENCE,
            name="Template reference parity (smoke check)",
            passed=None,
            skipped=True,
            evidence="no template reference staged",
        )
    actual_rows = _read_csv(actual_csv)
    ref_rows = _read_csv(reference_csv)
    if len(actual_rows) != len(ref_rows):
        # Parity is only meaningful when the user uploaded the
        # template's own sample. A different upload (different row
        # count) shares no rows with the reference; do NOT claim
        # parity in either direction.
        return ValidationCheck(
            layer=ValidationLayer.TEMPLATE_REFERENCE,
            name="Template reference parity (smoke check)",
            passed=None,
            skipped=True,
            evidence=(
                f"upload size ({len(actual_rows)} rows) differs from "
                f"the '{template_name}' template's reference "
                f"({len(ref_rows)} rows); parity smoke check is only "
                "meaningful for the template's own sample"
            ),
        )
    columns = compare_columns or sorted(
        set(actual_rows[0].keys()) & set(ref_rows[0].keys())
        if actual_rows and ref_rows
        else []
    )
    by_pk = {r.get(primary_key): r for r in ref_rows}
    drifts: list[str] = []
    matched = 0
    for r in actual_rows:
        pk = r.get(primary_key)
        ref = by_pk.get(pk)
        if ref is None:
            drifts.append(f"{primary_key}={pk}: missing in template reference")
            continue
        row_match = True
        for c in columns:
            if r.get(c) != ref.get(c):
                drifts.append(
                    f"{primary_key}={pk} col={c}: actual={r.get(c)!r} ref={ref.get(c)!r}"
                )
                row_match = False
                break
        if row_match:
            matched += 1
        if len(drifts) >= 20:
            break
    note = (
        f"reference is the '{template_name}' template's own generated "
        "output (generated fixture, NOT independent validation)"
    )
    if drifts:
        return ValidationCheck(
            layer=ValidationLayer.TEMPLATE_REFERENCE,
            name="Template reference parity (smoke check)",
            passed=False,
            evidence=f"{len(drifts)} drift(s) vs template reference",
            detail=note + "\n" + "\n".join(drifts[:20]),
            hint_if_failed=(
                "template-seeded code likely modified or re-implemented "
                "incorrectly"
            ),
        )
    return ValidationCheck(
        layer=ValidationLayer.TEMPLATE_REFERENCE,
        name="Template reference parity (smoke check)",
        passed=True,
        evidence=(
            f"{matched}/{len(actual_rows)} rows match template reference "
            f"on columns {columns}"
        ),
        detail=note,
    )


# ---------------------------------------------------------------------------
# Semantic rules — bank_categoriser
# ---------------------------------------------------------------------------

_ALLOWED_CATEGORIES = {
    "Income",
    "Office Expense",
    "Travel",
    "Subscriptions",
    "Refund",
    "Uncategorised",
}

_DESCRIPTION_RULES: list[tuple[str, str, str]] = [
    (
        r"\b(google|microsoft|aws|github|stripe|notion|figma|slack)\b",
        "Subscriptions",
        "subscription_pattern",
    ),
    (
        r"\b(uber|lyft|airbnb|train|airline|taxi|cab|bp|shell|esso|petrol|fuel)\b",
        "Travel",
        "travel_pattern",
    ),
    (
        r"\b(office|supplies|equipment|amazon|staples|stationery|printer|paper|furniture|whsmith)\b",
        "Office Expense",
        "office_pattern",
    ),
    (
        r"\b(salary|payroll|client\s+payment)\b",
        "Income",
        "income_pattern",
    ),
]


def layer_bank_categoriser_semantics(
    *,
    actual_csv: Path,
    category_column: str = "category",
) -> ValidationCheck:
    """Real semantic rule checks for bank_categoriser output.

    Asserts the bank-categoriser invariants the workflow promises:

      * Negative amounts must be tagged ``Refund``.
      * Description patterns map to a known category.
      * Every value of ``category`` is in the allowed enum.

    First-match-wins matches the template's documented cascade
    (rules.py): the refund override dominates, then description
    patterns in subscription→travel→office→income order.
    """
    import re

    if not actual_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.SEMANTIC_RULES,
            name="Semantic rules (bank categoriser)",
            passed=False,
            evidence=f"output file {actual_csv.name!r} not found",
        )
    rows = _read_csv(actual_csv)
    if not rows:
        return ValidationCheck(
            layer=ValidationLayer.SEMANTIC_RULES,
            name="Semantic rules (bank categoriser)",
            passed=False,
            evidence="output has zero rows",
        )

    violations: list[str] = []
    refund_checked = 0
    desc_checked = 0
    enum_checked = 0

    for row in rows:
        pk = _pk(row)
        category = (row.get(category_column) or "").strip()
        enum_checked += 1
        if category and category not in _ALLOWED_CATEGORIES:
            violations.append(f"{pk}: category={category!r} not in allowed enum")
            continue
        # Refund override
        try:
            amount = float(row.get("amount") or "0")
        except ValueError:
            amount = 0.0
        if amount < 0:
            refund_checked += 1
            if category != "Refund":
                violations.append(
                    f"{pk}: amount={amount} but category={category!r} "
                    "(rule: negative amount must be Refund)"
                )
                continue
            continue  # Refund override applies — skip description checks.

        description = (row.get("description") or "")
        for pattern, expected, _rule_name in _DESCRIPTION_RULES:
            if re.search(pattern, description, re.IGNORECASE):
                desc_checked += 1
                if category != expected:
                    violations.append(
                        f"{pk}: description matches {pattern!r} → expected "
                        f"{expected!r}, got {category!r}"
                    )
                break

    if violations:
        return ValidationCheck(
            layer=ValidationLayer.SEMANTIC_RULES,
            name="Semantic rules (bank categoriser)",
            passed=False,
            evidence=(
                f"{len(violations)} semantic violation(s) across "
                f"{len(rows)} rows"
            ),
            detail="\n".join(violations[:20]),
            hint_if_failed=(
                "the agent's category for these rows does not follow the "
                "documented rule cascade (refund override → description "
                "patterns → enum membership)"
            ),
        )
    return ValidationCheck(
        layer=ValidationLayer.SEMANTIC_RULES,
        name="Semantic rules (bank categoriser)",
        passed=True,
        evidence=(
            f"all {len(rows)} rows comply: enum={enum_checked}, "
            f"refund_override={refund_checked}, description_matched={desc_checked}"
        ),
        detail=(
            "checked: negative_amount→Refund; subscription/travel/office/"
            "income description patterns; category enum membership "
            f"({sorted(_ALLOWED_CATEGORIES)})"
        ),
    )


# ---------------------------------------------------------------------------
# Layer 6 — generated pytest
# ---------------------------------------------------------------------------


def layer_generated_pytest(
    *, test_results: TestResults | None
) -> ValidationCheck:
    if test_results is None:
        return ValidationCheck(
            layer=ValidationLayer.GENERATED_PYTEST,
            name="Generated pytest",
            passed=None,
            skipped=True,
            evidence=(
                "no pytest run performed (tests directory missing)"
            ),
        )
    collected = test_results.collected_count
    if collected <= 0 and test_results.per_test:
        collected = len(test_results.per_test)
    if collected <= 0 and test_results.total_count > 0:
        collected = test_results.total_count
    if collected < 1 or "no tests collected" in test_results.summary_line.lower():
        return ValidationCheck(
            layer=ValidationLayer.GENERATED_PYTEST,
            name="Generated pytest",
            passed=False,
            evidence="no tests collected",
            detail=test_results.summary_line,
            hint_if_failed=(
                "generated/tests/test_agent.py must define pytest-discoverable "
                "test_* functions with all required imports"
            ),
        )
    if collected < 3:
        return ValidationCheck(
            layer=ValidationLayer.GENERATED_PYTEST,
            name="Generated pytest",
            passed=False,
            evidence=f"only {collected} test(s) collected; need at least 3",
            detail=test_results.summary_line,
            hint_if_failed="add more contract-backed test_* functions",
        )
    if test_results.failed_count or test_results.error_count:
        failing = [
            t.name
            for t in test_results.per_test
            if t.status in ("failed", "error")
        ]
        return ValidationCheck(
            layer=ValidationLayer.GENERATED_PYTEST,
            name="Generated pytest",
            passed=False,
            evidence=(
                f"{test_results.failed_count} failed / "
                f"{test_results.passed_count} passed"
            ),
            detail="\n".join(failing) if failing else test_results.summary_line,
            hint_if_failed="fix the failing assertion(s) before finalising",
        )
    if test_results.passed_count < 1:
        return ValidationCheck(
            layer=ValidationLayer.GENERATED_PYTEST,
            name="Generated pytest",
            passed=False,
            evidence="no generated tests passed",
            detail=test_results.summary_line,
            hint_if_failed="ensure generated tests execute and pass",
        )
    return ValidationCheck(
        layer=ValidationLayer.GENERATED_PYTEST,
        name="Generated pytest",
        passed=True,
        evidence=(
            f"{test_results.passed_count}/{collected} collected tests passed"
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _read_header(path: Path) -> list[str]:
    with path.open("r", newline="") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return []


def _pk(row: dict[str, str]) -> str:
    """Best-guess primary-key value for an error message."""
    for candidate in ("txn_id", "invoice_id", "id"):
        if candidate in row:
            return f"{candidate}={row[candidate]}"
    return "<no-pk>"


__all__ = [
    "layer_bank_categoriser_semantics",
    "layer_business_rules",
    "layer_generated_pytest",
    "layer_golden_output",
    "layer_required_columns",
    "layer_row_level",
    "layer_schema",
    "layer_template_reference_parity",
]
