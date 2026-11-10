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
    """``present_tool_result``, or the caller's ``validation_error`` when a shape is refused."""
    try:
        return present_tool_result(binding, result, page)
    except Exception as exc:
        handler = exception_handler_for(exc, exception_map)
        if handler is not None:
            return answer_mapped_exception(handler, exc, config=config, span=span)
        if not isinstance(exc, ServiceValidationError):
            raise
        bound = binding.server_arguments.names()
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
