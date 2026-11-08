from __future__ import annotations

from typing import Any

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.protocol.types.tool_result import ToolResult


def answer_mapped_exception(
    handler: ExceptionHandler, exc: Exception, *, config: MCPConfig, span: Any = None
) -> ToolResult:
    """What ``handler`` answers ``exc`` with, recorded on the span as a service error is.

    A mapped exception ends the call inside a well-formed result, so the tracer
    never sees it leave: under ``RECORD_SERVICE_EXCEPTIONS`` it is recorded here,
    as the ``ServiceError`` arm records its own. Each conjunct is held by a test:
    ``test_a_mapped_exception_is_recorded_only_when_asked`` fails without the
    setting's, and an in-process ``call_tool``, which has no span, without the span's.

    Anything but a ``ToolResult`` is the handler's bug and is raised as one. A
    ``JsonRpcError`` in particular would otherwise reach the wire as a protocol error,
    and a mapped exception is never one
    (``test_a_handler_returning_anything_else_is_a_type_error``).
    """
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
