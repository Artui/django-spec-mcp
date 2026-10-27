from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind, SelectorSpec, UnsetType

from django_spec_mcp.auth.permissions.scope_required import ScopeRequired
from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding


def kernel_selector_to_resource(
    spec: Any,
    *,
    name: str,
    uri_template: str,
    description: str | None,
    title: str | None,
    icons: tuple[Icon, ...],
    rate_limits: tuple[Any, ...],
    annotations: dict[str, Any] | None,
    meta: dict[str, Any] | None,
    always_listed: bool,
    cache_ttl_ms: int | UnsetType,
    completions: dict[str, Callable[..., Any]] | None,
    scopes: tuple[str, ...] | None,
) -> KernelResourceBinding:
    """Build the binding ``MCPServer.register_resource`` registers for a kernel spec.

    Refuses a ``spec`` that is not a kernel ``SelectorSpec`` with a
    ``TypeError``, a ``LIST`` one, since a resource read answers one document
    for one URI, and ``scopes=()``, which reads as a gate and admits everyone.
    The URI-template variables are the arguments the read dispatches with, so
    the spec's ``reads`` name them.
    """
    if not isinstance(spec, SelectorSpec):
        raise TypeError(
            f"Resource {name!r}: register_resource takes a django_service_specs "
            f"SelectorSpec, not {type(spec).__module__}.{type(spec).__qualname__}. A "
            "drf-services selector registers through register_resource(selector=...)."
        )
    if spec.kind is not SelectorKind.RETRIEVE:
        raise ImproperlyConfigured(
            f"Resource {name!r}: a kernel resource reads one row, so its SelectorSpec "
            f"is a RETRIEVE, not a {spec.kind.name}. Register a LIST as a selector tool."
        )
    if scopes is not None and not scopes:
        raise ImproperlyConfigured(
            f"Resource {name!r}: scopes=() permits every caller while reading as a gate. "
            "Name at least one scope, or leave scopes=None for no scope gate."
        )
    return KernelResourceBinding(
        name=name,
        uri_template=uri_template,
        description=description,
        selector=spec.selector,
        kind=spec.kind,
        spec=spec,
        scopes=None if scopes is None else tuple(scopes),
        permissions=(ScopeRequired(list(scopes)),) if scopes else (),
        rate_limits=rate_limits,
        annotations=dict(annotations or {}),
        meta=dict(meta or {}),
        title=title,
        icons=icons,
        always_listed=always_listed,
        cache_ttl_ms=cache_ttl_ms,
        completions=dict(completions or {}),
    )


__all__ = ["kernel_selector_to_resource"]
