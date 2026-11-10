from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest
from django_service_specs import DispatchError, PoolSeeds, ServiceError, dispatch

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.answer_mapped_exception import answer_mapped_exception
from django_spec_mcp.handlers.exception_handler_for import exception_handler_for
from django_spec_mcp.handlers.kernel_utils import (
    admit_kernel_call,
    bound_unpaginated_rows,
    call_context,
    copy_request,
    not_found_result,
    page_request,
    principal_of,
    seat,
    tool_result,
)
from django_spec_mcp.handlers.present_kernel_result import present_kernel_result
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.handlers.utils import apply_tool_server_arguments
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.types.agent_conventions import AgentConventions


def call_kernel_tool(
    binding: KernelToolBinding,
    arguments: dict[str, Any],
    *,
    user: Any,
    request: Any,
    config: MCPConfig,
    conventions: AgentConventions,
    pool_seeds: PoolSeeds,
    request_classes: dict[tuple[type, frozenset[str]], type],
    exception_map: Mapping[type[Exception], ExceptionHandler],
) -> ToolResult:
    """The in-process ``call_tool`` of a kernel tool, through ``dispatch``."""
    if request is not None:
        request = copy_request(request, request_classes)
        vars(request).pop("mcp_token", None)
    else:
        request = HttpRequest()
        request.method = "POST"
    request = seat(request, principal_of(user))
    context = call_context(request, binding.name)
    arguments = apply_tool_server_arguments(binding, arguments, dispatch_context=context)
    dispatched, page = page_request(binding, arguments, config)
    try:
        admission = admit_kernel_call(
            binding,
            dispatched,
            user=user,
            config=config,
            pool_seeds=pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            context=context,
        )
        result = dispatch(
            binding.spec,
            principal=admission.principal,
            grant=admission.grant,
            seeds=admission.seeds,
            arguments=dispatched,
            pool_seeds=pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            call_context=admission.call_context,
        )
    except Exception as exc:
        handler = exception_handler_for(exc, exception_map)
        if handler is not None:
            return answer_mapped_exception(handler, exc, config=config)
        if not isinstance(exc, DispatchError | ServiceError):
            raise
        answer = answer_kernel_refusal(
            exc, KernelRefusalScene(arguments=arguments, config=config, conventions=conventions)
        )
        if not isinstance(answer, ToolResult):
            raise
        return answer
    if result.kind == "not_found":
        return not_found_result(binding)
    bounded = bound_unpaginated_rows(binding, result, page, config, exception_map=exception_map)
    if isinstance(bounded, ToolResult):
        return bounded
    payload: Any = present_kernel_result(
        binding,
        bounded,
        page,
        arguments=arguments,
        config=config,
        conventions=conventions,
        exception_map=exception_map,
    )
    if isinstance(payload, ToolResult):
        return payload
    return tool_result(binding, payload, config=config)


__all__ = ["call_kernel_tool"]
