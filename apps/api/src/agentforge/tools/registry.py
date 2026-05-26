"""Typed tool registry — the executor's only dispatch surface (INV-2).

The registry pairs a canonical :class:`ToolDefinition` (the serialisable
contract from :mod:`agentforge.schemas.tool`) with the runtime handler and
its Pydantic input/output classes. Splitting the handler off the
``ToolDefinition`` keeps INV-8 intact: the definition stays Pydantic-
serialisable (so it can land in events, audit exports, and the API
surface), while the live ``Callable`` lives on a non-Pydantic dataclass.

Idempotency keys are derived deterministically per INV-7:
``sha256(session_id + tool_name + step + canonical_args)``. The two
helpers below — :func:`canonical_args_json` and
:func:`derive_idempotency_key` — separate canonicalisation from hashing
so callers that already have canonical bytes (e.g., the executor in BP5
after Pydantic re-validation) can hash directly without round-tripping.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from agentforge.schemas import ToolDefinition, ToolPhase

# Function-argument contravariance: a handler that accepts a SPECIFIC
# BaseModel subclass (e.g. ``WriteFileInput``) is not, under mypy's
# strict rules, assignable to a slot typed ``Callable[[BaseModel, …]…]``.
# At runtime every handler is correct — the agent loop validates the
# typed input against ``tool.input_schema`` before dispatch, so the
# concrete BaseModel subclass is guaranteed. ``Callable[..., …]`` lets
# the registry accept any handler that returns an awaitable BaseModel.
ToolHandler = Callable[..., Awaitable[BaseModel]]


class ToolRegistrationError(RuntimeError):
    """Raised when a tool cannot be registered (duplicate, schema conflict)."""


class ToolNotFoundError(KeyError):
    """Raised when a lookup names a tool the registry does not hold.

    Distinct from :class:`agentforge.api.errors.ToolNotRegisteredError`,
    which is the user-facing API envelope. This is the internal lookup
    signal; the executor (BP5) translates it for the model.
    """


@dataclass
class RegisteredTool:
    """A live registry entry: the contract plus the things needed to dispatch.

    The handler is async even when the underlying work is synchronous
    (e.g., a CSV inspection) so the executor (BP5) has a single
    awaitable interface across read and execution tools. Sync handlers
    that block on subprocess work will use ``asyncio.to_thread``.
    """

    definition: ToolDefinition
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: ToolHandler


class ToolRegistry:
    """In-memory store of registered tools.

    The registry is constructed once per process via :func:`build_registry`
    and stored on ``app.state.tool_registry`` by the FastAPI lifespan.
    Tests construct fresh instances directly.
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    # ------------------------------------------------------------------
    # Mutation (boot-time only)
    # ------------------------------------------------------------------

    def register(self, tool: RegisteredTool) -> None:
        """Register a tool. Duplicate names raise :class:`ToolRegistrationError`."""
        name = tool.definition.name
        if name in self._tools:
            raise ToolRegistrationError(f"tool already registered: {name!r}")
        if tool.definition.input_schema_name != tool.input_schema.__name__:
            raise ToolRegistrationError(
                f"input_schema_name {tool.definition.input_schema_name!r} "
                f"does not match class {tool.input_schema.__name__!r}"
            )
        if tool.definition.output_schema_name != tool.output_schema.__name__:
            raise ToolRegistrationError(
                f"output_schema_name {tool.definition.output_schema_name!r} "
                f"does not match class {tool.output_schema.__name__!r}"
            )
        self._tools[name] = tool

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> RegisteredTool:
        """Return the registered tool. Raises :class:`ToolNotFoundError` if absent."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def all_names(self) -> list[str]:
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[RegisteredTool]:
        return iter(self._tools.values())

    # ------------------------------------------------------------------
    # Anthropic tool-use surface
    # ------------------------------------------------------------------

    def list_for_phase(self, phase: ToolPhase) -> list[dict[str, Any]]:
        """Return the Anthropic tool-use shape for tools enabled in ``phase``.

        Anthropic's API expects ``[{"name", "description", "input_schema"}]``;
        ``input_schema`` is a JSON Schema dict produced by Pydantic.
        ``output_schema_name`` is internal metadata and is not exposed to
        the model — the model only proposes the call.
        """
        result: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if phase in tool.definition.phases:
                result.append(_anthropic_shape(tool))
        # Stable order for prompt-cache hits.
        result.sort(key=lambda t: t["name"])
        return result


def _anthropic_shape(tool: RegisteredTool) -> dict[str, Any]:
    return {
        "name": tool.definition.name,
        "description": tool.definition.description,
        "input_schema": tool.input_schema.model_json_schema(),
    }


# ---------------------------------------------------------------------------
# Idempotency-key derivation (INV-7)
# ---------------------------------------------------------------------------


def canonical_args_json(args: BaseModel | dict[str, Any]) -> str:
    """Return a deterministic JSON serialisation of tool arguments.

    Pydantic models go through ``model_dump(mode='json')`` to coerce
    UUID/datetime/enum to JSON-safe primitives; then ``json.dumps`` with
    sorted keys and compact separators produces a byte-identical result
    for byte-identical inputs.
    """
    data = args.model_dump(mode="json") if isinstance(args, BaseModel) else args
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def derive_idempotency_key(
    *,
    session_id: UUID,
    tool_name: str,
    step: int,
    canonical_args: str,
) -> str:
    """``sha256(session_id + '|' + tool_name + '|' + step + '|' + canonical_args)``.

    The delimiter is a literal ``|`` (single byte not in UUID, tool name,
    or stringified step) so unequal tuples cannot collide via
    concatenation. ``canonical_args`` must be the output of
    :func:`canonical_args_json` for determinism.
    """
    payload = f"{session_id}|{tool_name}|{step}|{canonical_args}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_registry() -> ToolRegistry:
    """Construct the registry with all BP4 tools registered.

    BP5 extends this with write tools (``write_file``, ``apply_patch``)
    and execution tools (``run_python_script``, ``run_pytest``); BP6 adds
    the repair-flow tools. Each addition is a single ``register`` call.
    """
    # Imported inside the factory so test code can import the registry
    # types without pulling in pandas at import time.
    from agentforge.tools.archive_tool import ARCHIVE_WORKSPACE_TOOL
    from agentforge.tools.code_tools import APPLY_PATCH_TOOL, WRITE_FILE_TOOL
    from agentforge.tools.csv_tools import INSPECT_CSV_TOOL, INSPECT_XLSX_TOOL
    from agentforge.tools.execution_tools import (
        RUN_PYTEST_TOOL,
        RUN_PYTHON_SCRIPT_TOOL,
    )
    from agentforge.tools.repair_tools import (
        CLASSIFY_PROBLEM_TOOL,
        DIAGNOSE_TOOL,
        PROPOSE_PATCH_TOOL,
        RECORD_REPRODUCTION_TOOL,
        SUMMARISE_AGENT_PURPOSE_TOOL,
    )
    from agentforge.tools.user_tools import ASK_USER_TOOL
    from agentforge.tools.validation_tools import (
        FINALISE_SESSION_TOOL,
        GENERATE_REPAIR_REPORT_TOOL,
        GENERATE_VALIDATION_REPORT_TOOL,
        VALIDATE_OUTPUT_TOOL,
    )
    from agentforge.tools.workspace_tools import (
        INSPECT_FILE_TOOL,
        LIST_WORKSPACE_TOOL,
    )

    registry = ToolRegistry()
    for tool in (
        # Read tools (BP4)
        LIST_WORKSPACE_TOOL,
        INSPECT_FILE_TOOL,
        INSPECT_CSV_TOOL,
        INSPECT_XLSX_TOOL,
        ASK_USER_TOOL,
        # Approval-gated write tools (BP5b)
        WRITE_FILE_TOOL,
        APPLY_PATCH_TOOL,
        RUN_PYTHON_SCRIPT_TOOL,
        RUN_PYTEST_TOOL,
        # Validation + finalisation tools (BP5c)
        VALIDATE_OUTPUT_TOOL,
        GENERATE_VALIDATION_REPORT_TOOL,
        FINALISE_SESSION_TOOL,
        # Repair-flow tools (BP6)
        SUMMARISE_AGENT_PURPOSE_TOOL,
        CLASSIFY_PROBLEM_TOOL,
        RECORD_REPRODUCTION_TOOL,
        DIAGNOSE_TOOL,
        PROPOSE_PATCH_TOOL,
        GENERATE_REPAIR_REPORT_TOOL,
        # Archive packaging (BP10a)
        ARCHIVE_WORKSPACE_TOOL,
    ):
        registry.register(tool)
    return registry
