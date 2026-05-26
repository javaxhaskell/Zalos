"""Contract-driven validation for model-authored Author workflows."""

from __future__ import annotations

import ast
import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from agentforge.schemas import TestResults, ValidationCheck, ValidationLayer, ValidationReport
from agentforge.schemas.author_output_contract import (
    AggregationSpec,
    AuthorOutputContract,
    CalculatedFieldSpec,
    ExceptionRuleSpec,
    SummaryMetricSpec,
    output_file_path,
)
from agentforge.validation import (
    layer_generated_pytest,
    layer_golden_output,
    layer_required_columns,
    layer_row_level,
    layer_schema,
)


def validate_against_contract(
    *,
    session_id: UUID,
    workspace: Path,
    contract: AuthorOutputContract,
    test_results: TestResults | None,
    golden_csv: Path | None = None,
    golden_ignore_columns: tuple[str, ...] = (),
    golden_config_primary_key: str | None = None,
) -> tuple[ValidationReport, list[str]]:
    warnings = list(contract.warnings)
    checks: list[ValidationCheck] = []
    row_output = workspace / contract.row_level_output_file
    required_output_paths = set(contract.all_required_output_paths())

    checks.append(_layer_required_deliverables(workspace=workspace, contract=contract))
    checks.append(layer_schema(actual_csv=row_output, expected_columns=contract.output_columns))
    checks.append(
        layer_required_columns(
            actual_csv=row_output,
            required_columns=contract.required_output_columns,
        )
    )

    input_csv = workspace / contract.input_file
    if contract.input_format == "xlsx":
        normalised = workspace / "uploads/normalised_input.csv"
        input_csv = normalised if normalised.is_file() else input_csv
    checks.append(
        layer_row_level(
            actual_csv=row_output,
            input_csv=input_csv if input_csv.is_file() else None,
            primary_key=contract.primary_row_key,
        )
    )

    for field in contract.calculated_fields:
        if not str(field.formula or "").strip():
            continue
        checks.append(
            _layer_calculated_field(
                actual_csv=row_output,
                field=field,
                tolerance=Decimal(str(contract.tolerances.get(field.name, 0.01))),
            )
        )

    checks.append(
        _layer_allowed_enums(
            actual_csv=row_output,
            allowed_enums=contract.allowed_enums,
        )
    )

    for spec in contract.aggregation_specs:
        checks.append(
            _layer_summary_group_coverage(
                row_csv=row_output,
                summary_csv=workspace / spec.output_path,
                spec=spec,
            )
        )

    for entry in contract.exception_output_files:
        exception_rel = output_file_path(entry)
        checks.append(
            _layer_exception_consistency(
                row_csv=row_output,
                exceptions_csv=workspace / exception_rel,
                flag_columns=_exception_flag_columns(contract),
                required=exception_rel in required_output_paths,
                contract=contract,
            )
        )

    golden_required = contract.golden_comparison_requirement == "required"
    golden_path = golden_csv
    if contract.golden_output_path:
        candidate = workspace / contract.golden_output_path
        if candidate.is_file():
            golden_path = candidate
    checks.append(
        layer_golden_output(
            actual_csv=row_output,
            golden_csv=golden_path if golden_required else None,
            contract_primary_row_key=contract.primary_row_key,
            golden_config_primary_key=golden_config_primary_key,
            ignore_columns=golden_ignore_columns,
        )
    )
    checks.append(layer_generated_pytest(test_results=test_results))

    overall = all(check.passed is True for check in checks if not check.skipped)
    report = ValidationReport(
        session_id=session_id,
        generated_at=_utcnow(),
        overall_passed=overall,
        checks=checks,
    )
    return report, warnings


def _utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _layer_required_deliverables(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> ValidationCheck:
    missing = [
        rel
        for rel in contract.all_required_output_paths()
        if not (workspace / rel).is_file()
    ]
    return ValidationCheck(
        layer=ValidationLayer.SCHEMA,
        name="Required deliverables present",
        passed=not missing,
        evidence=(
            f"missing deliverables: {missing}"
            if missing
            else f"all required outputs present ({len(contract.all_required_output_paths())})"
        ),
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _layer_calculated_field(
    *,
    actual_csv: Path,
    field: CalculatedFieldSpec,
    tolerance: Decimal,
) -> ValidationCheck:
    rows = _read_csv(actual_csv)
    if not rows:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name=f"Calculated field {field.name}",
            passed=False,
            evidence="row output missing or empty",
        )
    if field.name not in rows[0]:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name=f"Calculated field {field.name}",
            passed=False,
            evidence=f"output column {field.name!r} missing",
        )

    try:
        expression = _compile_formula(field.formula)
    except ValueError as exc:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name=f"Calculated field {field.name}",
            passed=False,
            evidence=f"formula is not executable by contract validator: {exc}",
        )

    checked = 0
    mismatches: list[str] = []
    skipped = 0
    for idx, row in enumerate(rows, start=2):
        env: dict[str, Decimal] = {}
        for column, value in row.items():
            parsed = _decimal(value)
            if parsed is not None:
                env[_formula_name(column)] = parsed
        try:
            expected = _eval_formula(expression, env)
        except KeyError:
            skipped += 1
            continue
        actual = _decimal(row.get(field.name))
        if actual is None:
            mismatches.append(f"row {idx}: missing {field.name}")
            continue
        checked += 1
        if abs(actual - expected) > tolerance:
            mismatches.append(f"row {idx}: {actual} != {expected}")

    return ValidationCheck(
        layer=ValidationLayer.BUSINESS_RULES,
        name=f"Calculated field {field.name}",
        passed=checked > 0 and not mismatches,
        skipped=checked == 0 and not mismatches,
        evidence=(
            f"checked={checked}; skipped={skipped}; mismatches={mismatches[:5]}"
            if mismatches
            else f"checked={checked}; skipped={skipped}"
        ),
    )


def _compile_formula(formula: str) -> ast.Expression:
    try:
        parsed = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ValueError(str(exc)) from exc
    for node in ast.walk(parsed):
        if isinstance(node, ast.Expression | ast.BinOp | ast.UnaryOp | ast.Load):
            continue
        if isinstance(node, ast.Name | ast.Constant):
            continue
        if isinstance(node, ast.Add | ast.Sub | ast.Mult | ast.Div | ast.USub | ast.UAdd):
            continue
        raise ValueError(f"unsupported formula syntax: {type(node).__name__}")
    return parsed


def validate_supported_formula(formula: str) -> None:
    """Raise ValueError when the formula is outside the supported validator DSL."""
    _compile_formula(formula)


def _formula_name(column: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in column.strip())


def _eval_formula(expression: ast.Expression, env: dict[str, Decimal]) -> Decimal:
    def eval_node(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int | float | str):
                return Decimal(str(node.value))
            raise ValueError(f"unsupported literal {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise KeyError(node.id)
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            value = eval_node(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError(f"unsupported formula node {type(node).__name__}")

    return eval_node(expression)


_MULTI_VALUE_ENUM_COLUMNS = frozenset({"rule_used"})
_MULTI_VALUE_ENUM_SEPARATORS = (";",)


def _split_list_like_enum_tokens(value: str) -> list[str]:
    text = value.strip()
    if not text:
        return []
    for separator in _MULTI_VALUE_ENUM_SEPARATORS:
        if separator in text:
            return [part.strip() for part in text.split(separator) if part.strip()]
    return [text]


def _allowed_enum_tokens_allowed(
    *,
    column: str,
    value: str,
    allowed_set: set[str],
) -> bool:
    if column not in _MULTI_VALUE_ENUM_COLUMNS:
        return value in allowed_set
    tokens = _split_list_like_enum_tokens(value)
    if not tokens:
        return True
    lowered_allowed = {item.lower() for item in allowed_set}
    return all(token.lower() in lowered_allowed for token in tokens)


def _layer_allowed_enums(
    *,
    actual_csv: Path,
    allowed_enums: dict[str, list[str]],
) -> ValidationCheck:
    rows = _read_csv(actual_csv)
    violations: list[str] = []
    for column, allowed in allowed_enums.items():
        allowed_set = set(allowed)
        for idx, row in enumerate(rows, start=2):
            value = (row.get(column) or "").strip()
            if value and not _allowed_enum_tokens_allowed(
                column=column,
                value=value,
                allowed_set=allowed_set,
            ):
                violations.append(f"row {idx}: {column}={value!r}")
    return ValidationCheck(
        layer=ValidationLayer.BUSINESS_RULES,
        name="Allowed enum values",
        passed=not violations,
        skipped=not allowed_enums,
        evidence="; ".join(violations[:5]) if violations else "enum checks passed",
    )


def _layer_summary_group_coverage(
    *,
    row_csv: Path,
    summary_csv: Path,
    spec: AggregationSpec,
) -> ValidationCheck:
    if not summary_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Summary group coverage",
            passed=False,
            evidence=f"summary file missing: {summary_csv.name}",
        )
    rows = _read_csv(row_csv)
    summary_rows = _read_csv(summary_csv)
    if not rows:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Summary group coverage",
            passed=False,
            evidence="row output empty",
        )
    if spec.group_key not in rows[0]:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Summary group coverage",
            passed=False,
            evidence=f"group key {spec.group_key!r} not present in row-level output",
        )
    if summary_rows and spec.group_key not in summary_rows[0]:
        return ValidationCheck(
            layer=ValidationLayer.BUSINESS_RULES,
            name="Summary group coverage",
            passed=False,
            evidence=f"group key {spec.group_key!r} not present in summary output",
        )

    grouped = {
        (row.get(spec.group_key) or "").strip() or "(empty)"
        for row in rows
    }
    summary_groups = {
        (row.get(spec.group_key) or "").strip() or "(empty)"
        for row in summary_rows
    }
    missing = sorted(grouped - summary_groups)
    return ValidationCheck(
        layer=ValidationLayer.BUSINESS_RULES,
        name="Summary group coverage",
        passed=not missing,
        evidence=(
            f"summary missing groups: {missing[:5]}"
            if missing
            else f"summary covers {len(grouped)} groups"
        ),
    )


def _layer_exception_consistency(
    *,
    row_csv: Path,
    exceptions_csv: Path,
    flag_columns: list[str],
    required: bool,
    contract: AuthorOutputContract,
) -> ValidationCheck:
    if not exceptions_csv.is_file():
        return ValidationCheck(
            layer=ValidationLayer.ROW_LEVEL,
            name="Exception list consistency",
            passed=False if required else None,
            skipped=not required,
            evidence=(
                f"exception file missing: {exceptions_csv.name}"
                if required
                else f"optional exception file not produced: {exceptions_csv.name}"
            ),
        )
    rows = _read_csv(row_csv)
    exceptions = _read_csv(exceptions_csv)
    flag_column = next((column for column in flag_columns if rows and column in rows[0]), None)
    if flag_column is None:
        return ValidationCheck(
            layer=ValidationLayer.ROW_LEVEL,
            name="Exception list consistency",
            passed=False,
            evidence=f"no contract flag column present; candidates={flag_columns}",
        )
    flagged = [
        row
        for row in rows
        if _is_row_flagged(row.get(flag_column), flag_column=flag_column, contract=contract)
    ]
    primary_key = contract.primary_row_key
    if len(exceptions) != len(flagged):
        evidence = f"exception count {len(exceptions)} != flagged rows {len(flagged)}"
        passed = False
    elif primary_key and rows and primary_key in rows[0]:
        flagged_keys = {row.get(primary_key, "") for row in flagged}
        exception_keys = {row.get(primary_key, "") for row in exceptions}
        if flagged_keys != exception_keys:
            missing = sorted(flagged_keys - exception_keys)
            extra = sorted(exception_keys - flagged_keys)
            evidence = f"exception keys mismatch: missing={missing} extra={extra}"
            passed = False
        else:
            passed = True
            evidence = f"{len(exceptions)} exception rows match flagged output rows"
    else:
        passed = True
        evidence = f"{len(exceptions)} exception rows match flagged output rows"
    return ValidationCheck(
        layer=ValidationLayer.ROW_LEVEL,
        name="Exception list consistency",
        passed=passed,
        evidence=evidence,
    )


def _exception_flag_columns(contract: AuthorOutputContract) -> list[str]:
    candidates = [
        column
        for column in contract.output_columns
        if column in {"issue_flag", "exception_flag", "review_flag", "flagged"}
        or column.endswith("_flag")
    ]
    for rule in contract.exception_rules:
        if isinstance(rule, ExceptionRuleSpec) and rule.output_column:
            candidates.append(rule.output_column)
    return candidates or ["issue_flag", "exception_flag", "review_flag"]


_NON_FLAGGED_ENUM_HINTS = frozenset(
    {
        "no",
        "no_issue",
        "no_exception",
        "ok",
        "none",
        "false",
        "0",
        "n",
        "pass",
        "passed",
        "clean",
    }
)
_QUOTED_VALUE_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")


def _summary_metric_filter(metric: str | SummaryMetricSpec) -> str | None:
    """Return the filter expression for a summary metric, if structured."""
    if isinstance(metric, str):
        return None
    return metric.filter


def _summary_metric_name(metric: str | SummaryMetricSpec) -> str:
    if isinstance(metric, str):
        return metric
    return metric.name


def _parse_column_equality_filter(filter_expr: str, column: str) -> str | None:
    pattern = re.compile(
        rf"^\s*{re.escape(column)}\s*==\s*['\"]([^'\"]+)['\"]\s*$",
        re.IGNORECASE,
    )
    match = pattern.match(filter_expr.strip())
    return match.group(1) if match else None


def _extract_quoted_literals(text: str) -> set[str]:
    return {match.group(1) or match.group(2) for match in _QUOTED_VALUE_RE.finditer(text)}


def _contract_non_flagged_values(flag_column: str, contract: AuthorOutputContract) -> set[str]:
    non_flagged: set[str] = set()
    allowed = contract.allowed_enums.get(flag_column, [])

    for metric in contract.summary_metrics:
        metric_filter = _summary_metric_filter(metric)
        if not metric_filter:
            continue
        eq_val = _parse_column_equality_filter(metric_filter, flag_column)
        if eq_val is None:
            continue
        name = _summary_metric_name(metric).lower()
        if any(token in name for token in ("no_issue", "no-issue", "not_flagged", "clean", "passed", "ok")):
            non_flagged.add(eq_val)

    for spec in contract.output_column_semantics:
        if spec.name != flag_column:
            continue
        if spec.fallback_value_semantics:
            non_flagged.update(_extract_quoted_literals(spec.fallback_value_semantics))

    for value in allowed:
        if value.lower() in _NON_FLAGGED_ENUM_HINTS:
            non_flagged.add(value)

    return non_flagged


def _contract_flagged_values_from_metrics(
    flag_column: str,
    contract: AuthorOutputContract,
) -> set[str]:
    flagged: set[str] = set()
    for metric in contract.summary_metrics:
        metric_filter = _summary_metric_filter(metric)
        if not metric_filter:
            continue
        eq_val = _parse_column_equality_filter(metric_filter, flag_column)
        if eq_val is None:
            continue
        name = _summary_metric_name(metric).lower()
        if any(token in name for token in ("exception", "flagged", "review", "issue")):
            if not any(token in name for token in ("no_issue", "no-issue", "not_flagged")):
                flagged.add(eq_val)
    return flagged


def _contract_flagged_values(flag_column: str, contract: AuthorOutputContract) -> set[str] | None:
    from_metrics = _contract_flagged_values_from_metrics(flag_column, contract)
    if from_metrics:
        return from_metrics

    allowed = contract.allowed_enums.get(flag_column)
    if not allowed:
        return None

    non_flagged = _contract_non_flagged_values(flag_column, contract)
    if not non_flagged:
        return None

    flagged = {value for value in allowed if value.lower() not in {item.lower() for item in non_flagged}}
    if not flagged:
        return None

    if len(allowed) <= 2 and len(flagged) == 1:
        return flagged

    for spec in contract.output_column_semantics:
        if spec.name == flag_column and spec.producer_kind == "exception_flag":
            if flagged and non_flagged:
                return flagged

    return None


def _is_row_flagged(
    value: str | None,
    *,
    flag_column: str,
    contract: AuthorOutputContract,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False

    flagged_values = _contract_flagged_values(flag_column, contract)
    if flagged_values is not None:
        return text.lower() in {item.lower() for item in flagged_values}

    return _truthy(text)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1", "y", "flagged"}


def production_outputs_need_agent_restore(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> bool:
    """True when pytest or fixture runs left production CSVs inconsistent."""
    if not contract.preserve_row_count:
        return False
    row_output = workspace / contract.row_level_output_file
    input_path = workspace / contract.input_file
    if not row_output.is_file() or not input_path.is_file():
        return False
    input_rows = _read_csv(input_path)
    output_rows = _read_csv(row_output)
    if input_rows and len(input_rows) != len(output_rows):
        return True
    if not contract.exception_output_files:
        return False
    exception_rel = output_file_path(contract.exception_output_files[0])
    exceptions_csv = workspace / exception_rel
    if not exceptions_csv.is_file():
        return False
    flag_columns = _exception_flag_columns(contract)
    flag_column = next(
        (column for column in flag_columns if output_rows and column in output_rows[0]),
        None,
    )
    if flag_column is None:
        return False
    flagged = [
        row
        for row in output_rows
        if _is_row_flagged(row.get(flag_column), flag_column=flag_column, contract=contract)
    ]
    exceptions = _read_csv(exceptions_csv)
    if len(exceptions) != len(flagged):
        return True
    primary_key = contract.primary_row_key
    if primary_key and output_rows and primary_key in output_rows[0]:
        flagged_keys = {row.get(primary_key, "") for row in flagged}
        exception_keys = {row.get(primary_key, "") for row in exceptions}
        return flagged_keys != exception_keys
    return False
