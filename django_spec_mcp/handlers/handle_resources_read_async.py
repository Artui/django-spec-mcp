from __future__ import annotations

from typing import Any, cast

from django_service_specs import base_pool, resolve_callable_kwargs

from django_spec_mcp._compat.acall import acall
from django_spec_mcp._compat.tracing import span
from django_spec_mcp._compat.utils import arun_selector_sync_safe
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import KernelAdmission, admit_kernel_read, carry_token
from django_spec_mcp.handlers.read_kernel_resource_async import read_kernel_resource_async
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


async def handle_resources_read_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``handle_resources_read``.

    Same shape, but nothing that can touch the ORM runs on the event loop:
    the permission check, the kernel admission and the rate limits go
    through ``acall``, and each read path keeps its selector and its
    rendering off the loop too.
    """
    target = resolve_resource_read(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, vars_, uri = target

    with span(
        "mcp.resources.read",
        attributes={**span_attrs(binding.name, context), "mcp.resource.uri": uri},
    ):
        allowed, required_scopes = await acall(judge_binding, binding, context.token)
        if not allowed:
            return forbidden(required_scopes)

        # See the sync sibling: a kernel resource's spec's own checks, before
        # the charge, off the loop because a check may query.
        admission: KernelAdmission | None = None
        if runs_on_kernel(binding):
            admitted = await acall(
                admit_kernel_read, cast(KernelResourceBinding, binding), vars_, context
            )
            if isinstance(admitted, JsonRpcError):
                return admitted
            admission = admitted

        retry_after: int | None = await acall(
            consume_rate_limits, binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after)

        # See the sync sibling: a kernel resource reads through the kernel from
        # here, under the admission its spec's checks gave.
        if admission is not None:
            return await read_kernel_resource_async(
                cast(KernelResourceBinding, binding), uri, vars_, admission, context
            )
        # See the sync sibling: the kernel's pool and the transport's request.
        # The selector goes through ``arun_selector_sync_safe`` and the encoding
        # through ``acall``, so neither a template it renders nor a lazy value
        # ``JSON`` encoding iterates is read on the loop.
        request = carry_token(context.http_request, context.token)
        pool: dict[str, Any] = {
            **vars_,
            **base_pool(user=context.token.user, request=request, seeds=context.pool_seeds),
        }
        raw: Any = await arun_selector_sync_safe(
            binding.selector, resolve_callable_kwargs(binding.selector, pool)
        )
        contents: ResourceContents | JsonRpcError = await acall(
            build_resource_contents, binding=binding, uri=uri, raw=raw
        )
        if isinstance(contents, JsonRpcError):
            return contents
        return resource_read_answer(contents, binding, uri, context)


__all__ = ["handle_resources_read_async"]
