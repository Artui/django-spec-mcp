from __future__ import annotations

from typing import Any

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.protocol.types.tool_result import ToolResult


def answer_mapped_exception(
    handler: ExceptionHandler, exc: Exception, *, config: MCPConfig, span: Any = None
) -> ToolResult:
    """What ``handler`` answers ``exc`` with, recorded on the span as a service error is."""
    if span is not None and config.record_service_exceptions:
        span.record_exception(exc)
    answer = handler(exc)
    if not isinstance(answer, ToolResult):
        raise TypeError(
            f"The exception_map handler for {type(exc).__name__} returned "
            f"{type(answer).__name__}, not a ToolResult."
        )
    return answer


__all__ = ["answer_mapped_exception"]
