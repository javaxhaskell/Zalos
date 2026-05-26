"""Typed tool registry — the executor's only dispatch surface (INV-2).

Public surface for the rest of the backend. Handlers and per-tool input
schemas live in the domain modules (``workspace_tools``, ``csv_tools``,
later ``code_tools``, ``execution_tools``, etc.).
"""

from __future__ import annotations

from agentforge.tools.base import ToolContext
from agentforge.tools.registry import (
    RegisteredTool,
    ToolHandler,
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRegistry,
    build_registry,
    canonical_args_json,
    derive_idempotency_key,
)

__all__ = [
    "RegisteredTool",
    "ToolContext",
    "ToolHandler",
    "ToolNotFoundError",
    "ToolRegistrationError",
    "ToolRegistry",
    "build_registry",
    "canonical_args_json",
    "derive_idempotency_key",
]
