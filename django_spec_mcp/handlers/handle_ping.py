from __future__ import annotations

from typing import Any

from django_spec_mcp.handlers.types.context import MCPCallContext


def handle_ping(params: dict[str, Any] | None, context: MCPCallContext) -> dict[str, Any]:
    """Respond to the MCP ``ping`` keepalive."""
    del params, context
    return {}


__all__ = ["handle_ping"]
