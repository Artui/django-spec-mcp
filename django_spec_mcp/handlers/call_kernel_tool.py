from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, PoolSeeds, ServiceError, dispatch

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    not_found_result,
    page_request,
    present_tool_result,
    principal_of,
    tool_result,
    unguarded_grant,
)
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
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
) -> ToolResult:
    """The in-process ``call_tool`` of a kernel tool, through ``dispatch``.

    ``arguments`` already carry the binding's server arguments. The scope gate
    is not judged here: there is no token in process, as there is none for the
    drf-services path's in-process call, whose transport permissions it skips
    alike. The spec's own permissions are the kernel's, and ``dispatch`` asks
    them of ``user``.

    A refusal the wire answers ``-32006`` is raised as it is, the
    ``NotPermitted`` or ``PrincipalUnavailable`` itself, for the caller to map
    to its own wire; every other refusal comes back as the ``isError`` result
    the wire serves. ``AdditionalInputRequired`` is a service error here, since
    there is no further round to ask in.
    """
    dispatched, page = page_request(binding, arguments, config)
    principal: Any = principal_of(user)
    try:
        result = dispatch(
            binding.spec,
            principal=principal,
            grant=unguarded_grant(binding.spec, principal),
            arguments=dispatched,
            pool_seeds=pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            call_context=call_context(request, binding.name),
        )
    except (DispatchError, ServiceError) as exc:
        answer = answer_kernel_refusal(
            exc, KernelRefusalScene(arguments=arguments, config=config, conventions=conventions)
        )
        if not isinstance(answer, ToolResult):
            raise
        return answer
    if result.kind == "not_found":
        return not_found_result(binding)
    payload: Any = present_tool_result(binding, result, page)
    return tool_result(binding, payload, output_format=None, config=config)


__all__ = ["call_kernel_tool"]
