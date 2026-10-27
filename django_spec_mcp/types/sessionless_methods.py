"""``SESSIONLESS_METHODS`` - methods answerable before a session exists."""

from __future__ import annotations

SESSIONLESS_METHODS: frozenset[str] = frozenset({"initialize", "server/discover"})
"""Methods answerable before a session exists.

The legacy transport requires an ``Mcp-Session-Id`` on everything except the
request that mints one. ``server/discover`` joins it because its purpose is to
be the first thing a client sends. Both are exempt from the
``MCP-Protocol-Version`` header requirement for the same reason, and only
``initialize`` mints a session.
"""
