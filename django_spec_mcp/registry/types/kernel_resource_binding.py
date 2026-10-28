from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django_service_specs import SelectorSpec

from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.registry.types.utils import kernel_scope_gate


@dataclass(frozen=True, kw_only=True)
class KernelResourceBinding(ResourceBinding[Any]):
    """A resource whose read is a kernel ``RETRIEVE`` ``SelectorSpec``.

    A ``ResourceBinding`` in every listing respect, so ``resources/list``,
    ``resources/templates/list``, completions and subscriptions read it as they
    read any other. ``resources/read`` runs ``spec`` through the kernel's
    ``dispatch`` / ``adispatch`` instead of calling ``selector`` directly, so the
    spec's own permissions, reads, shaping and presenter answer the read.
    ``mime_type`` and ``encoding`` are a resource's as on any binding, applied
    to what the presenter returns: ``TEXT`` serves its ``str`` verbatim and
    ``BLOB`` base64-encodes its bytes, on a binding built by hand as on a
    registered one.

    ``selector`` is the spec's, set at registration, and nothing on this path
    calls it itself. ``permissions`` is not a constructor
    argument here: it holds only the scope gate built from ``scopes``, derived
    when the binding is built, so one built by hand and passed to
    ``server.resources.register`` gates its reads as a registered one does
    (``test_a_handbuilt_kernel_resource_gates_on_its_scopes``). The spec's
    ``permissions`` are the kernel's and ``dispatch`` asks them.
    """

    spec: SelectorSpec
    scopes: tuple[str, ...] | None = None
    """See ``KernelToolBinding.scopes``. A bare string is one scope, and ``()`` is
    refused, since it reads as a gate and admits every caller."""
    permissions: tuple[Any, ...] = field(init=False)
    """The scope gate the shared shell judges, derived from ``scopes``."""
    unguarded: bool = field(default=False, init=False)
    """Whether a call runs the spec without a permission check.

    Set only by the registrar, when it registers a spec declaring
    ``permissions=None`` with ``REQUIRE_TOOL_PERMISSIONS`` off, so nothing can set
    it at construction: a binding built by hand stays unmarked, and the kernel's
    dispatch refuses its unguarded spec
    (``test_a_handbuilt_unguarded_kernel_resource_is_never_read``)."""

    def __post_init__(self) -> None:
        scopes, checks = kernel_scope_gate(f"Resource {self.name!r}", self.scopes)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "permissions", checks)


__all__ = ["KernelResourceBinding"]
