from __future__ import annotations

from collections.abc import Callable

from django_spec_mcp.protocol.types.tool_result import ToolResult

ExceptionHandler = Callable[[Exception], ToolResult]
"""What an ``MCPServer(exception_map=...)`` entry maps an exception type to.

Handed the exception, and returns the tool's result, so the caller still gets a
well-formed ``tools/call`` response. Synchronous on both transports: the async one
runs it off the event loop, so it may read the database."""


__all__ = ["ExceptionHandler"]
