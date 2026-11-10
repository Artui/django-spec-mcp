from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django_service_specs import UNSET, PermissionCheck, UnsetType

from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.utils import binding_checks, scope_names
from django_spec_mcp.types.resource_encoding import ResourceEncoding

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ResourceBinding(Generic[ResultT]):
    """All wiring for a single MCP view: a resource read by calling a selector."""

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

    permissions: tuple[PermissionCheck, ...] = ()
    """The kernel ``PermissionCheck``s a caller must pass, every one of them.

    A view is not a spec, so nothing dispatches it: each check is asked
    ``has_permission(principal, binding)`` with this binding as the spec
    (``handlers.judge_binding``), at ``resources/list``, ``resources/read``, a
    completion and a subscription's grant alike. Held to the kernel's rule for
    a spec's (``registry.types.utils.binding_checks``), but for one case: an
    empty sequence here is no checks, the field's default, and only
    ``register_ui_resource`` refuses an explicit ``[]``."""
    scopes: tuple[str, ...] | None = None
    """The OAuth scopes a caller's token must carry, every one of them, asked
    before ``permissions``. ``None`` is no scope gate; a bare string is one scope,
    and ``()`` is refused, as on ``KernelToolBinding.scopes``."""
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

    def __post_init__(self) -> None:
        label = f"Resource {self.name!r}"
        object.__setattr__(self, "scopes", scope_names(label, self.scopes))
        object.__setattr__(self, "permissions", binding_checks(label, self.permissions or None))

    @property
    def is_template(self) -> bool:
        return "{" in self.uri_template


__all__ = ["ResourceBinding"]
