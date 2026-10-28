from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django_service_specs import DispatchError, PoolSeeds, ServiceError, dispatch

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.kernel_utils import (
    admit_kernel_call,
    bound_unpaginated_rows,
    copy_request,
    not_found_result,
    page_request,
    principal_of,
    seat,
    tool_result,
)
from django_spec_mcp.handlers.present_kernel_result import present_kernel_result
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
    alike. The spec's own permissions are the kernel's, asked of ``user``
    before ``dispatch`` checks the arguments, as on the wire
    (``admit_kernel_call``), so a caller they refuse is told that rather than
    which argument it left out
    (``test_call_tool_refuses_a_caller_the_spec_denies_before_the_argument_is_checked``);
    ``dispatch`` runs under the grant they gave.

    A refusal the wire answers ``-32006`` is raised as it is, the
    ``NotPermitted`` or ``PrincipalUnavailable`` itself, for the caller to map
    to its own wire; every other refusal comes back as the ``isError`` result
    the wire serves. ``AdditionalInputRequired`` is a service error here, since
    there is no further round to ask in.
    """
    # A handed request is the caller's own, so the spec is handed a copy whose
    # ``user`` is the one the call runs as, as ``acall_tool``'s is
    # (``test_sync_call_tool_hands_a_copy_carrying_the_acting_user``); with
    # none handed, a minimal one bearing that user is synthesised, as
    # ``acall_tool`` and the drf-services path do
    # (``test_sync_call_tool_with_no_request_hands_one_carrying_the_acting_user``).
    if request is not None:
        request = copy_request(request)
    else:
        request = HttpRequest()
        request.method = "POST"
    request = seat(request, principal_of(user))
    dispatched, page = page_request(binding, arguments, config)
    try:
        # Inside the ``try``, so the one thing it raises, a denial, meets the
        # arm below, which raises it on as it raises dispatch's own.
        admission = admit_kernel_call(
            binding,
            dispatched,
            user=user,
            request=request,
            config=config,
            pool_seeds=pool_seeds,
            unknown_arguments=binding.unknown_arguments,
        )
        result = dispatch(
            binding.spec,
            principal=admission.principal,
            grant=admission.grant,
            arguments=dispatched,
            pool_seeds=pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            call_context=admission.call_context,
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
    bounded = bound_unpaginated_rows(binding, result, page, config)
    if isinstance(bounded, ToolResult):
        return bounded
    payload: Any = present_kernel_result(
        binding, bounded, page, arguments=arguments, config=config, conventions=conventions
    )
    if isinstance(payload, ToolResult):
        return payload
    return tool_result(binding, payload, output_format=None, config=config)


__all__ = ["call_kernel_tool"]
