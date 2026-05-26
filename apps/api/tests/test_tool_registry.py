"""Unit tests for the typed tool registry (Build Prompt 4).

Covers:
  * Pure helpers (``canonical_args_json``, ``derive_idempotency_key``)
    are deterministic and independent of dict insertion order.
  * Idempotency keys differ when any of (session_id, tool_name, step,
    args) differs — that is the INV-7 contract that drives the
    BP5 idempotency store.
  * The registry enforces uniqueness on tool names and that
    ``input_schema_name`` / ``output_schema_name`` match the actual
    classes (cheap protection against contract drift).
  * ``list_for_phase`` returns Anthropic tool-use shape — name +
    description + JSON-Schema input — sorted by name for cache
    stability.
  * ``build_registry`` ships exactly the four BP4 read tools with the
    expected phase exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from agentforge.schemas import RiskLevel, StrictModel, ToolDefinition, ToolPhase
from agentforge.tools import (
    RegisteredTool,
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRegistry,
    build_registry,
    canonical_args_json,
    derive_idempotency_key,
)

_AUTHORIZE = "agentforge.tools.authz.allow_authenticated_users"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _TestInput(StrictModel):
    a: int
    b: str


class _TestOutput(StrictModel):
    ok: bool


async def _dummy_handler(args: BaseModel, ctx: Any) -> BaseModel:  # noqa: ARG001
    return _TestOutput(ok=True)


def _make_tool(
    name: str = "dummy",
    *,
    phases: list[ToolPhase] | None = None,
    input_schema_name: str = "_TestInput",
    output_schema_name: str = "_TestOutput",
) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            name=name,
            description="dummy read tool for tests",
            input_schema_name=input_schema_name,
            output_schema_name=output_schema_name,
            risk_level=RiskLevel.READ,
            requires_approval=False,
            idempotent=True,
            phases=phases or [ToolPhase.AUTHOR_INFO],
            authorize_callable=_AUTHORIZE,
        ),
        input_schema=_TestInput,
        output_schema=_TestOutput,
        handler=_dummy_handler,
    )


# ---------------------------------------------------------------------------
# canonical_args_json
# ---------------------------------------------------------------------------


def test_canonical_args_json_is_independent_of_dict_insertion_order() -> None:
    a = {"x": 1, "y": [3, 2, 1], "z": {"nested": True}}
    b = {"z": {"nested": True}, "y": [3, 2, 1], "x": 1}
    assert canonical_args_json(a) == canonical_args_json(b)


def test_canonical_args_json_serialises_pydantic_models() -> None:
    model = _TestInput(a=42, b="hello")
    serialised = canonical_args_json(model)
    # Compact + sorted: keys appear alphabetically with no spaces.
    assert serialised == '{"a":42,"b":"hello"}'


def test_canonical_args_json_handles_json_native_types() -> None:
    # UUIDs come through Pydantic's mode='json' as strings; raw dicts must
    # be JSON-safe by the time canonicalisation runs.
    sid = UUID("12345678-1234-5678-1234-567812345678")
    out = canonical_args_json({"sid": str(sid), "n": 7})
    assert out == '{"n":7,"sid":"12345678-1234-5678-1234-567812345678"}'


# ---------------------------------------------------------------------------
# derive_idempotency_key
# ---------------------------------------------------------------------------


@dataclass
class _IdemBundle:
    session_id: UUID
    tool_name: str
    step: int
    canonical_args: str


def _key(b: _IdemBundle) -> str:
    return derive_idempotency_key(
        session_id=b.session_id,
        tool_name=b.tool_name,
        step=b.step,
        canonical_args=b.canonical_args,
    )


def test_derive_idempotency_key_is_deterministic() -> None:
    sid = uuid4()
    b1 = _IdemBundle(sid, "list_workspace", 3, '{"path":"."}')
    b2 = _IdemBundle(sid, "list_workspace", 3, '{"path":"."}')
    assert _key(b1) == _key(b2)
    # Hex sha256: 64 chars
    assert len(_key(b1)) == 64


def test_derive_idempotency_key_differs_on_any_input_change() -> None:
    sid = uuid4()
    other_sid = uuid4()
    base = _IdemBundle(sid, "list_workspace", 3, '{"path":"."}')

    keys = {_key(base)}
    keys.add(_key(_IdemBundle(other_sid, base.tool_name, base.step, base.canonical_args)))
    keys.add(_key(_IdemBundle(base.session_id, "inspect_file", base.step, base.canonical_args)))
    keys.add(_key(_IdemBundle(base.session_id, base.tool_name, base.step + 1, base.canonical_args)))
    keys.add(
        _key(_IdemBundle(base.session_id, base.tool_name, base.step, '{"path":"uploads"}'))
    )
    # Five distinct combinations → five distinct hashes.
    assert len(keys) == 5


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


def test_registry_starts_empty() -> None:
    reg = ToolRegistry()
    assert len(reg) == 0
    assert reg.all_names() == []
    assert not reg.has("anything")


def test_registry_register_then_get() -> None:
    reg = ToolRegistry()
    tool = _make_tool("dummy")
    reg.register(tool)
    assert len(reg) == 1
    assert reg.has("dummy")
    assert reg.get("dummy") is tool


def test_registry_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("dummy"))
    with pytest.raises(ToolRegistrationError, match="already registered"):
        reg.register(_make_tool("dummy"))


def test_registry_mismatched_input_schema_name_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolRegistrationError, match="input_schema_name"):
        reg.register(_make_tool("dummy", input_schema_name="WrongName"))


def test_registry_mismatched_output_schema_name_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolRegistrationError, match="output_schema_name"):
        reg.register(_make_tool("dummy", output_schema_name="WrongName"))


def test_registry_get_missing_raises_tool_not_found() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.get("nope")


def test_list_for_phase_returns_anthropic_shape_sorted_by_name() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("zeta", phases=[ToolPhase.AUTHOR_INFO]))
    reg.register(_make_tool("alpha", phases=[ToolPhase.AUTHOR_INFO]))
    reg.register(_make_tool("beta", phases=[ToolPhase.REPAIR_FIX]))

    info = reg.list_for_phase(ToolPhase.AUTHOR_INFO)
    assert [t["name"] for t in info] == ["alpha", "zeta"]
    for entry in info:
        assert set(entry) == {"name", "description", "input_schema"}
        # Pydantic-generated JSON Schema carries 'properties'
        assert "properties" in entry["input_schema"]

    fix = reg.list_for_phase(ToolPhase.REPAIR_FIX)
    assert [t["name"] for t in fix] == ["beta"]


# ---------------------------------------------------------------------------
# build_registry — BP4 tool set
# ---------------------------------------------------------------------------


def test_build_registry_ships_all_currently_registered_tools() -> None:
    """The registered set grows per build prompt; this asserts the current
    snapshot so additions/removals are visible in a diff."""
    reg = build_registry()
    assert reg.all_names() == sorted([
        # Read tools (BP4)
        "inspect_csv_schema",
        "inspect_file",
        "inspect_xlsx_schema",
        "list_workspace",
        "ask_user",
        # Approval-gated write tools (BP5b)
        "write_file",
        "apply_patch",
        "run_python_script",
        "run_pytest",
        # Validation + finalisation tools (BP5c)
        "validate_output",
        "generate_validation_report",
        "finalise_session",
        # Repair-flow tools (BP6)
        "summarise_agent_purpose",
        "classify_problem",
        "record_reproduction",
        "diagnose",
        "propose_patch",
        "generate_repair_report",
        # Archive packaging (BP10a)
        "archive_workspace",
    ])


def test_build_registry_phase_exposure_matches_contracts() -> None:
    reg = build_registry()

    info_names_author = [t["name"] for t in reg.list_for_phase(ToolPhase.AUTHOR_INFO)]
    assert "inspect_csv_schema" in info_names_author
    assert "inspect_xlsx_schema" in info_names_author
    assert "ask_user" in info_names_author

    build_names = [t["name"] for t in reg.list_for_phase(ToolPhase.AUTHOR_BUILD)]
    # CSV/XLSX inspectors are info-only per CONTRACTS.md §4.
    assert "inspect_csv_schema" not in build_names
    assert "inspect_xlsx_schema" not in build_names
    assert "seed_template" not in build_names
    # list_workspace + inspect_file remain available in build/fix phases.
    assert "list_workspace" in build_names
    assert "inspect_file" in build_names


def test_build_registry_every_tool_has_resolvable_authorize() -> None:
    """INV-4 + INV-8: every tool carries a dotted-path authorize callable."""
    reg = build_registry()
    for tool in reg:
        assert tool.definition.authorize_callable == _AUTHORIZE
        assert tool.definition.idempotent is True


def test_build_registry_write_tools_cite_adr_when_auto_approved() -> None:
    """Per INV-4: a write tool with ``requires_approval=False`` MUST cite
    an ADR in ``adr_override``."""
    reg = build_registry()
    for tool in reg:
        defn = tool.definition
        if defn.risk_level in (RiskLevel.LOW_WRITE, RiskLevel.HIGH_WRITE) and not defn.requires_approval:
            assert defn.adr_override is not None, (
                f"tool {defn.name!r} is a write tool with auto-approve but no "
                f"adr_override citation; INV-4 requires one"
            )
