from __future__ import annotations

from typing import Any, cast

from django_service_specs import base_pool, resolve_callable_kwargs

from django_spec_mcp._compat.acall import acall
from django_spec_mcp._compat.tracing import span
from django_spec_mcp._compat.utils import arun_selector_sync_safe
from django_spec_mcp.auth.permissions.utils import DispatchShape
from django_spec_mcp.handlers.handle_tools_call import _span_attrs
from django_spec_mcp.handlers.kernel_utils import KernelAdmission, admit_kernel_read, carry_token
from django_spec_mcp.handlers.read_drfs_resource_async import read_drfs_resource_async
from django_spec_mcp.handlers.read_kernel_resource_async import read_kernel_resource_async
from django_spec_mcp.handlers.reads_through_drfs import reads_through_drfs
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    resolve_bound,
    resource_cache_hints,
    resource_not_found_code,
    resource_shape,
)
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.output.enforce_result_bytes import enforce_result_bytes
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


async def handle_resources_read_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``handle_resources_read``.

    Same shape, but nothing that can touch the ORM runs on the event loop:
    the permission check, the kernel admission and the rate limits go
    through ``acall``, and each read path keeps its selector and its
    rendering off the loop too
    ([`read_drfs_resource_async`][django_spec_mcp.handlers.read_drfs_resource_async.read_drfs_resource_async]
    says how).
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "resources/read params must be an object"
        )
    uri: Any = params.get("uri")
    if not isinstance(uri, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'uri' is required and must be a string"
        )

    resolved = context.resources.resolve(uri)
    if resolved is None:
        # See the sync sibling: ``-32002`` + ``data.uri`` is the spec's shape.
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Unknown resource: {uri!r}",
            data={"uri": uri},
        )
    binding, vars_ = resolved

    with span(
        "mcp.resources.read",
        attributes={**_span_attrs(binding.name, context), "mcp.resource.uri": uri},
    ):
        # See the sync sibling: a view reads plainly and builds no shape, and
        # otherwise the check and the view are built from one.
        plain: bool = not runs_on_kernel(binding) and not reads_through_drfs(binding)
        shape: DispatchShape | None = None if plain else resource_shape(binding, vars_)
        allowed, required_scopes = await acall(
            check_permissions,
            binding.permissions,
            context.http_request,
            context.token,
            shape=shape,
        )
        if not allowed:
            return JsonRpcError(
                JsonRpcErrorCode.FORBIDDEN,
                "Insufficient permission",
                data={"requiredScopes": required_scopes} if required_scopes else None,
            )

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
            return JsonRpcError(
                JsonRpcErrorCode.RATE_LIMITED,
                "Rate limit exceeded",
                data={"retryAfter": retry_after},
            )

        # See the sync sibling: a kernel resource reads through the kernel from
        # here, under the admission its spec's checks gave.
        if admission is not None:
            return await read_kernel_resource_async(
                cast(KernelResourceBinding, binding), uri, vars_, admission, context
            )
        contents: ResourceContents | JsonRpcError
        if plain:
            # See the sync sibling: the kernel's pool and the transport's
            # request. The selector goes through ``arun_selector_sync_safe``
            # and the encoding through ``acall``, so neither a template it
            # renders nor a lazy value ``JSON`` encoding iterates is read on
            # the loop.
            request = carry_token(context.http_request, context.token)
            pool: dict[str, Any] = {
                **vars_,
                **base_pool(user=context.token.user, request=request, seeds=context.pool_seeds),
            }
            raw: Any = await arun_selector_sync_safe(
                binding.selector, resolve_callable_kwargs(binding.selector, pool)
            )
            contents = await acall(build_resource_contents, binding=binding, uri=uri, raw=raw)
        else:
            contents = await read_drfs_resource_async(
                binding, uri, vars_, cast(DispatchShape, shape), context
            )
        if isinstance(contents, JsonRpcError):
            return contents
        result: dict[str, Any] = {
            "contents": [contents.to_dict()],
            **resource_cache_hints(
                resolve_bound(binding.cache_ttl_ms, context.config.resource_cache_ttl_ms)
            ),
        }
        # See the sync sibling: an over-ceiling read has no ``isError`` envelope
        # to live in, so it is a protocol error carrying the same message.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Resource {uri!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_resources_read_async"]
