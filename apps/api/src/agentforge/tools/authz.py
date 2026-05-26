"""Authorize callables referenced by ``ToolDefinition.authorize_callable``.

BP4 introduces a single permissive callable, ``allow_authenticated_users``,
which every read tool registers as its authorize target. The single-user
demo identity ``user-demo`` (see :mod:`agentforge.config`) is always
authenticated, so this is a no-op pass.

BP5/6 will introduce risk-level-aware authorize callables (e.g., a
``finance_user_only`` for high_write tools). At that point this file
grows; for now the single stub keeps the dotted-path strings on every
``ToolDefinition`` resolvable instead of dangling.
"""

from __future__ import annotations

from agentforge.tools.base import ToolContext


class ForbiddenError(Exception):
    """Raised by an authorize callable to deny a tool invocation."""


def allow_authenticated_users(ctx: ToolContext) -> None:
    """No-op authorize for the single-user demo identity.

    The executor (BP5) invokes this before dispatch; the contract is to
    raise :class:`ForbiddenError` on deny and return ``None`` on allow.
    """
    # The single demo identity is always authenticated in this prototype.
    # Real multi-tenant authorization is documented as a production
    # extension in docs/adr/0010-scope-exclusions.md.
    del ctx
