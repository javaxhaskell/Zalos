"""Author template materialisation is not available as a tool."""

from __future__ import annotations

from agentforge.schemas import ToolPhase
from agentforge.tools import build_registry


def test_seed_template_tool_is_not_registered_for_author() -> None:
    registry = build_registry()
    assert "seed_template" not in registry.all_names()
    assert "seed_template" not in [
        tool["name"] for tool in registry.list_for_phase(ToolPhase.AUTHOR_INFO)
    ]
    assert "seed_template" not in [
        tool["name"] for tool in registry.list_for_phase(ToolPhase.AUTHOR_BUILD)
    ]
