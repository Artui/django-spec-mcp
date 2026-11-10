from __future__ import annotations

from typing import Any, cast

from django_service_specs import resolve_callable_kwargs, run_selector, seeded_pool

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    admit_kernel_read,
    call_context,
    carry_token,
)
from django_spec_mcp.handlers.read_kernel_resource import read_kernel_resource
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    consume_rate_limits,
    forbidden,
    rate_limited,
    resolve_resource_read,
    resource_read_answer,
    span_attrs,
)
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding


def handle_resources_read(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Read a resource (or templated-resource instance) by URI."""
    target = resolve_resource_read(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, vars_, uri = target

    with span(
        "mcp.resources.read",
        attributes={**span_attrs(binding.name, context), "mcp.resource.uri": uri},
    ):
        allowed, required_scopes = judge_binding(binding, context.token)
        if not allowed:
            return forbidden(required_scopes)

        admission: KernelAdmission | None = None
        if runs_on_kernel(binding):
            admitted = admit_kernel_read(cast(KernelResourceBinding, binding), vars_, context)
            if isinstance(admitted, JsonRpcError):
                return admitted
            admission = admitted

        retry_after: int | None = consume_rate_limits(
            binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after)

        if admission is not None:
            return read_kernel_resource(
                cast(KernelResourceBinding, binding), uri, vars_, admission, context
            )
        request = carry_token(context.http_request, context.token)
        seeded = seeded_pool(
            user=context.token.user,
            seeds=context.pool_seeds,
            call_context=call_context(request, binding.name),
        )
        seeded.setdefault("request", request)
        pool: dict[str, Any] = {**vars_, **seeded}
        raw: Any = run_selector(binding.selector, resolve_callable_kwargs(binding.selector, pool))
        contents: ResourceContents | JsonRpcError = build_resource_contents(
            binding=binding, uri=uri, raw=raw
        )
        if isinstance(contents, JsonRpcError):
            return contents
        return resource_read_answer(contents, binding, uri, context)


__all__ = ["handle_resources_read"]
