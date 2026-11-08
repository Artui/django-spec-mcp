"""``MCP_TRANSPORT`` - the value a call over this package's transport reads as its transport."""

from __future__ import annotations

MCP_TRANSPORT: str = "mcp"
"""What ``call_transport(call_context)`` answers for every call this package serves.

A plain string, because the kernel lists no transports: each package owns its
own value and stamps it on the context it builds. The value is the transport the
call came in on, so a tool an agent reaches through ``acall_tool`` reads ``mcp``
too (``test_a_call_proxied_through_acall_tool_reads_mcp``)."""


__all__ = ["MCP_TRANSPORT"]
