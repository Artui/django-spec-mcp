from __future__ import annotations

from typing import Any

from django_service_specs import UNSET, base_pool, run_selector
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import resolve_callable_kwargs

from django_spec_mcp.auth.permissions.utils import DispatchShape
from django_spec_mcp.handlers.guard_resource_object import guard_resource_object
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.output.render_drfs_resource_output import render_drfs_resource_output
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


def read_drfs_resource(
    binding: ResourceBinding,
    uri: str,
    variables: dict[str, str],
    shape: DispatchShape,
    context: MCPCallContext,
) -> ResourceContents | JsonRpcError:
    """Read a resource lifted from a drf-services ``SelectorSpec``, into one ``contents`` block.

    Once ``handle_resources_read`` has judged the binding's permissions on
    ``shape`` and charged its rate limits. Everything here is drf-services':
    the offline request and view built from ``shape``, the ``kwargs`` provider
    called with them, the object-level guard and the output serializer, so
    this module is the read path that goes with drf-services.

    **This path does not route through ``dispatch_spec``.** A
    [`ResourceBinding`][django_spec_mcp.registry.types.resource_binding.ResourceBinding]
    holds a bare selector callable, not the spec it was lifted from, so there
    is no spec to dispatch. What ``dispatch_spec`` would have contributed is
    named explicitly here instead: the reserved seeds outrank the client's
    URI-template variables, the ``kwargs`` provider honours its ``UNSET``
    decline contract, and
    [`guard_resource_object`][django_spec_mcp.handlers.guard_resource_object.guard_resource_object]
    stands in for ``on_target_resolved``. Queryset shaping and the spec's
    ``preconditions`` are **not** reproduced — they never survive registration.
    """
    # URI-template variables ride on ``view.kwargs`` so a provider (and
    # the output serializer's context) reads them without re-parsing the
    # URI. ``auth`` beside ``user``, as on a tool's dispatch view: reading
    # ``request.auth`` on a view without it reset the caller to
    # ``AnonymousUser``, so a ``TokenHasScope``-style permission the check
    # admitted refused every caller at the guard.
    offline = shape.build(
        user=context.token.user, auth=context.token.raw, http_request=context.http_request
    )
    drf_request = offline.request
    view = offline.view

    # The transport's seeds land *after* the URI-template variables, so a
    # template variable named ``user`` or ``request`` -- or after a seed the
    # server's ``pool_seeds=`` registers -- cannot stand in for the value
    # the transport resolved. ``register_resource`` already refuses such a
    # template; this is the second lock, for a binding registered straight
    # onto the registry. Built through ``base_pool``, as drf-services asks
    # of every adapter assembling its own pool, so a selector reading a
    # registered seed receives it here as it does on a selector tool.
    pool: dict[str, Any] = {
        **variables,
        **base_pool(user=context.token.user, request=drf_request, seeds=context.pool_seeds),
    }
    if binding.kwargs_provider is not None:
        # ``SelectorSpec.kwargs``, invoked through the keyword pool exactly
        # as drf-services invokes it on the HTTP path: by name, so
        # ``def kwargs(request): ...`` works here too — including its
        # decline contract, so a provider that cannot resolve a key
        # off-HTTP returns ``UNSET`` and leaves the URI's own value
        # standing, rather than overwriting it with the sentinel.
        provider_pool: dict[str, Any] = {"view": view, "request": drf_request}
        provided: dict[str, Any] = binding.kwargs_provider(
            **resolve_callable_kwargs(binding.kwargs_provider, provider_pool)
        )
        pool.update({key: value for key, value in provided.items() if value is not UNSET})
    kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
    raw: Any = run_selector(binding.selector, kwargs)

    # Object-level permissions, on the row the selector actually resolved.
    # The class-level pass cannot see it, and this path has no
    # ``dispatch_spec`` to carry ``on_target_resolved`` for it.
    try:
        guard_resource_object(binding, raw, offline)
    except PermissionDenied:
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")

    payload: Any = render_drfs_resource_output(binding, raw, view=view, request=drf_request)
    return build_resource_contents(binding=binding, uri=uri, raw=payload)


__all__ = ["read_drfs_resource"]
