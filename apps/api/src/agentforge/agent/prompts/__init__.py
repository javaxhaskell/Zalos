"""System prompt loaders.

Prompts ship as ``.md`` files alongside this module so they're installed
as package data and can be cached by Anthropic's prompt cache without
the file path leaking into the cache key. The loaders read once and
return a plain string the model client passes through to Anthropic's
``system`` field.

If we ever templatise these prompts (per-template, per-user, etc.) the
templated portion lives outside the cached prefix — see
``shared/prompt-caching.md`` § "Frozen system prompt".
"""

from __future__ import annotations

from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent


def load_author_prompt() -> str:
    """Return the verbatim author-workflow system prompt."""
    return (_THIS_DIR / "author.md").read_text(encoding="utf-8")


def load_repair_prompt() -> str:
    """Return the verbatim repair-workflow system prompt (BP6 stub today)."""
    return (_THIS_DIR / "repair.md").read_text(encoding="utf-8")


__all__ = ["load_author_prompt", "load_repair_prompt"]
