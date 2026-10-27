from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, ServiceError, coerce_flat, dispatch, present

from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    carry_token,
    resource_read_result,
    resource_refusal,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding


def read_kernel_resource(
    binding: KernelResourceBinding,
    uri: str,
    variables: dict[str, str],
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Read a kernel resource through ``dispatch``, once the scope gate and the
    rate limits have answered in ``handle_resources_read``.

    The URI-template variables are the arguments, read from the strings a URI
    carries into the types the spec's parameters declare (``coerce_flat``), so
    ``invoices://7`` reaches an ``integer`` ``pk`` as ``7``.
    """
    try:
        result = dispatch(
            binding.spec,
            principal=context.token.user,
            arguments=coerce_flat(binding.spec.parameters(), variables),
            pool_seeds=context.pool_seeds,
            call_context=call_context(
                carry_token(context.http_request, context.token), binding.name
            ),
        )
    except (DispatchError, ServiceError) as exc:
        return resource_refusal(
            exc,
            KernelRefusalScene(
                arguments=variables, config=context.config, conventions=context.conventions
            ),
        )
    payload: Any = None if result.kind == "not_found" else present(binding.spec, result)
    return resource_read_result(binding, uri, result, payload, context)


__all__ = ["read_kernel_resource"]
