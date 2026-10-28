from __future__ import annotations

from typing import Any

from django_service_specs import UNSET, base_pool
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import resolve_callable_kwargs

from django_spec_mcp._compat.acall import acall
from django_spec_mcp._compat.utils import arun_selector_sync_safe
from django_spec_mcp.auth.permissions.utils import DispatchShape
from django_spec_mcp.handlers.guard_resource_object import guard_resource_object
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.output.render_drfs_resource_output import render_drfs_resource_output
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


async def read_drfs_resource_async(
    binding: ResourceBinding,
    uri: str,
    variables: dict[str, str],
    shape: DispatchShape,
    context: MCPCallContext,
) -> ResourceContents | JsonRpcError:
    """Async sibling of ``read_drfs_resource``.

    Nothing that can touch the ORM runs on the event loop: the selector goes
    through ``arun_selector_sync_safe`` (async selectors run native, sync ones
    are bridged) and the render + encode step through ``acall``, because a
    selector returning a queryset returns it **lazy** and the serializer is
    what evaluates it. The object-permission guard runs off the loop for the
    same reason: ``has_object_permission`` may query.
    """
    # See the sync sibling: URI-template variables ride on ``view.kwargs``,
    # and ``auth`` beside ``user`` keeps the caller.
    offline = shape.build(
        user=context.token.user, auth=context.token.raw, http_request=context.http_request
    )
    drf_request = offline.request
    view = offline.view

    # See the sync sibling: the transport's seeds, the server's
    # ``pool_seeds`` among them, land after the URI-template variables, so
    # none can be shadowed from the URI.
    pool: dict[str, Any] = {
        **variables,
        **base_pool(user=context.token.user, request=drf_request, seeds=context.pool_seeds),
    }
    if binding.kwargs_provider is not None:
        # ``SelectorSpec.kwargs``, invoked by name through the keyword pool
        # as on the HTTP path, ``UNSET`` decline included. It is sync — a
        # spec is written once for both transports — and its headline use
        # is a scoping tenant / role query, so it runs off the loop.
        provider_pool: dict[str, Any] = {"view": view, "request": drf_request}
        provided: dict[str, Any] = await acall(
            binding.kwargs_provider,
            **resolve_callable_kwargs(binding.kwargs_provider, provider_pool),
        )
        pool.update({key: value for key, value in provided.items() if value is not UNSET})
    kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
    raw: Any = await arun_selector_sync_safe(binding.selector, kwargs)

    # See the sync sibling: object-level permissions on the resolved row.
    # ``has_object_permission`` may query, so it runs off the loop.
    try:
        await acall(guard_resource_object, binding, raw, offline)
    except PermissionDenied:
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")

    # Rendering is ORM work: ``output_serializer(...).data`` iterates the
    # value, evaluating a lazy queryset right here. Off the loop, like the
    # selector above.
    payload: Any = await acall(
        render_drfs_resource_output, binding, raw, view=view, request=drf_request
    )
    return build_resource_contents(binding=binding, uri=uri, raw=payload)


__all__ = ["read_drfs_resource_async"]
