from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django_service_specs import UNSET, UnsetType

from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.types.resource_encoding import ResourceEncoding

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ResourceBinding(Generic[ResultT]):
    """All wiring for a single MCP view: a resource read by calling a selector.

    A view is a selector callable plus a URI. ``register_ui_resource`` builds
    one for an MCP Apps UI resource, and a kernel spec is a
    ``KernelResourceBinding`` instead. The MCP server invokes the selector
    directly via ``resolve_callable_kwargs`` + ``run_selector`` over the
    kernel's pool and the URI-template variables, and ``mime_type`` advertises
    the type the body will carry. ``annotations`` and ``meta`` are emitted
    verbatim on the listing entry, and ``meta`` also on the ``contents`` block
    of ``resources/read``.

    The generic parameter is purely informational, letting callers pin the
    selector's return type for type-checker help.
    """

    name: str
    uri_template: str
    description: str | None
    selector: Callable[..., ResultT]
    mime_type: str = "application/json"
    encoding: ResourceEncoding = ResourceEncoding.JSON
    """How the selector's value becomes the ``resources/read`` body. Declared rather
    than inferred from ``mime_type``, so advertising a new mime type never silently
    changes the encoding. See
    [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding]."""

    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # Free-form for the reason given on ``KernelToolBinding.meta``.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    completions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    """Argument name to completer callable, powering ``completion/complete``.

    A completer is dispatched through ``resolve_callable_kwargs`` against a
    pool of ``value`` (the text typed so far), ``arguments`` (siblings the
    client has already resolved, also spread by name), ``request`` and
    ``user``. It returns an iterable of suggestions — a list, a generator or a
    queryset — which the handler slices to the spec's cap rather than
    draining."""

    cache_ttl_ms: int | UnsetType = UNSET
    """How long a client may cache this resource's body, in milliseconds.
    ``UNSET`` takes the server's ``RESOURCE_CACHE_TTL_MS``, ``0`` by default
    because a resource body is live data. Worth setting on anything genuinely
    static: hosts prefetch interactive views before any tool call, so a zero
    TTL means fetching the same HTML repeatedly."""

    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this resource's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    always_listed: bool = False
    """Keep this resource in ``resources/list`` (or ``resources/templates/list``) even
    when ``FILTER_LISTINGS_BY_PERMISSIONS`` would drop it, as a tool's
    ``always_listed`` keeps it in ``tools/list``."""

    @property
    def is_template(self) -> bool:
        return "{" in self.uri_template


__all__ = ["ResourceBinding"]
