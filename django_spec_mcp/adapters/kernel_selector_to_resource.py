from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind, SelectorSpec, UnsetType

from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.utils import derived_resource_body, scope_names
from django_spec_mcp.types.resource_encoding import ResourceEncoding


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
    scopes: str | Sequence[str] | None,
    mime_type: str | UnsetType,
    encoding: ResourceEncoding | UnsetType,
) -> KernelResourceBinding:
    """Build the binding ``MCPServer.register_resource`` registers for a kernel spec."""
    if not isinstance(spec, SelectorSpec):
        raise TypeError(
            f"Resource {name!r}: register_resource takes a django_service_specs "
            f"SelectorSpec, not {type(spec).__module__}.{type(spec).__qualname__}."
        )
    if spec.kind is not SelectorKind.RETRIEVE:
        raise ImproperlyConfigured(
            f"Resource {name!r}: a kernel resource reads one row, so its SelectorSpec "
            f"is a RETRIEVE, not a {spec.kind.name}. Register a LIST as a selector tool."
        )
    encoding, mime_type = derived_resource_body(f"Resource {name!r}", spec, encoding, mime_type)
    return KernelResourceBinding(
        name=name,
        uri_template=uri_template,
        description=description,
        selector=spec.selector,
        spec=spec,
        scopes=scope_names(f"Resource {name!r}", scopes),
        rate_limits=rate_limits,
        annotations=dict(annotations or {}),
        meta=dict(meta or {}),
        title=title,
        icons=icons,
        always_listed=always_listed,
        cache_ttl_ms=cache_ttl_ms,
        completions=dict(completions or {}),
        mime_type=mime_type,
        encoding=encoding,
    )


__all__ = ["kernel_selector_to_resource"]
