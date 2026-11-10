from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django_service_specs import PermissionCheck, SelectorSpec

from django_spec_mcp.registry.types.resource_binding import ResourceBinding


@dataclass(frozen=True, kw_only=True)
class KernelResourceBinding(ResourceBinding[Any]):
    """A resource whose read is a kernel ``RETRIEVE`` ``SelectorSpec``."""

    spec: SelectorSpec
    scopes: tuple[str, ...] | None = None
    """See ``KernelToolBinding.scopes``. A bare string is one scope, and ``()`` is
    refused, since it reads as a gate and admits every caller."""
    permissions: tuple[PermissionCheck, ...] = field(default=(), init=False)
    """Always empty, so no check can sit beside the spec's own, which ``dispatch``
    asks: a view's checks are asked by the transport, a spec's by the kernel."""
    unguarded: bool = field(default=False, init=False)
    """Whether a call runs the spec without a permission check.

    Set only by the registrar, when it registers a spec declaring
    ``permissions=None`` with ``REQUIRE_TOOL_PERMISSIONS`` off, so nothing can set
    it at construction: a binding built by hand stays unmarked, and the kernel's
    dispatch refuses its unguarded spec
    (``test_a_handbuilt_unguarded_kernel_resource_is_never_read``)."""


__all__ = ["KernelResourceBinding"]
