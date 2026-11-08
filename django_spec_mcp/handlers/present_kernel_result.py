from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs import DispatchResult, ServiceValidationError

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.answer_mapped_exception import answer_mapped_exception
from django_spec_mcp.handlers.exception_handler_for import exception_handler_for
from django_spec_mcp.handlers.kernel_utils import KernelPageRequest, present_tool_result
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.handlers.utils import rendering_refusal_result
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.types.agent_conventions import AgentConventions


def present_kernel_result(
    binding: KernelToolBinding,
    result: DispatchResult,
    page: KernelPageRequest | None,
    *,
    arguments: dict[str, Any],
    config: MCPConfig,
    conventions: AgentConventions,
    exception_map: Mapping[type[Exception], ExceptionHandler],
    span: Any = None,
) -> Any:
    """``present_tool_result``, or the caller's ``validation_error`` when a shape is refused.

    A spec's ``shapes`` are read while the result is presented, after
    ``dispatch`` has returned, so a presenter refusing one fails outside every
    arm that decides whether a failure is the caller's to fix. So a
    ``ServiceValidationError`` from the presenter is answered through ``rendering_refusal_result``: an
    ``isError`` ``validation_error`` naming the shapes the caller sent, and the
    error raised unchanged when they sent none. An exception the server's
    ``exception_map`` holds answers as its handler says, asked first as at dispatch,
    since presenting a call is part of running it. Anything else a presenter raises is
    the server's and escapes as it did.

    "Sent" is read off ``arguments``, the call's own, less every name the
    binding's ``server_arguments`` answers for, because a value the server
    supplied is not the caller's to correct; a ``null`` is not sent. Every path a kernel tool is presented on calls this:
    ``run_kernel_tool``, its async sibling and the in-process
    ``call_kernel_tool``.
    """
    try:
        return present_tool_result(binding, result, page)
    except Exception as exc:
        handler = exception_handler_for(exc, exception_map)
        if handler is not None:
            return answer_mapped_exception(handler, exc, config=config, span=span)
        if not isinstance(exc, ServiceValidationError):
            raise
        bound = binding.server_arguments.names()
        # ``is not None`` and the bound names are each their own condition,
        # held by ``test_an_explicit_null_is_not_supplied`` and
        # ``test_a_shape_the_server_binds_is_not_supplied``.
        supplied: list[str] = [
            shape.name
            for shape in binding.spec.shapes
            if shape.name not in bound and arguments.get(shape.name) is not None
        ]
        refusal: ToolResult = rendering_refusal_result(
            exc,
            supplied=supplied,
            arguments=arguments,
            paginated=binding.paginate,
            config=config,
            conventions=conventions,
        )
        return refusal


__all__ = ["present_kernel_result"]
