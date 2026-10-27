from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_service_specs import SelectorSpec

from django_spec_mcp.registry.types.resource_binding import ResourceBinding


@dataclass(frozen=True, kw_only=True)
class KernelResourceBinding(ResourceBinding[Any]):
    """A resource whose read is a kernel ``RETRIEVE`` ``SelectorSpec``.

    A ``ResourceBinding`` in every listing respect, so ``resources/list``,
    ``resources/templates/list``, completions and subscriptions read it as they
    read any other. ``resources/read`` runs ``spec`` through the kernel's
    ``dispatch`` / ``adispatch`` instead of calling ``selector`` directly, so the
    spec's own permissions, reads, shaping and presenter answer the read.

    ``selector`` and ``kind`` are the spec's, set at registration, and nothing on
    this path calls ``selector`` itself. ``permissions`` holds only the scope
    gate built from ``scopes``; the spec's ``permissions`` are the kernel's and
    ``dispatch`` asks them.
    """

    spec: SelectorSpec
    scopes: tuple[str, ...] | None = None
    """See ``KernelToolBinding.scopes``."""


__all__ = ["KernelResourceBinding"]
