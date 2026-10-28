from __future__ import annotations

from typing import Any

from django_service_specs import SelectorKind
from rest_framework_services import base_serializer_context, renderable_serializer_class

from django_spec_mcp.registry.types.resource_binding import ResourceBinding


def render_drfs_resource_output(
    binding: ResourceBinding, raw: Any, *, view: Any, request: Any
) -> Any:
    """A selector's return value rendered through the binding's ``output_serializer``.

    Shared by ``read_drfs_resource`` and its async sibling, which are full
    parallel implementations rather than wrappers, so the render cannot drift
    between the two transports. ``raw`` comes back as it is when the binding
    declares no serializer; otherwise it renders with ``many=True`` for a
    ``LIST`` selector. The serializer gets DRF's baseline context (``request``
    / ``format`` / ``view``) built from the synthesized ``view`` / ``request``,
    so a field reading ``self.context["request"]`` resolves it as it would
    behind a view. A resource binding has no context provider of its own: its
    selector is a bare callable, not a spec.

    The declaration is resolved through drf-services'
    ``renderable_serializer_class`` before it is instantiated, as
    ``render_spec_output`` resolves it on the tool path. This site cannot
    delegate to that function — it takes a spec, and the binding holds none —
    so it makes the same resolution itself, rather than wrapping a dataclass
    here. A raw dataclass declared as the output therefore renders through a
    ``DataclassSerializer``; instantiated as declared, its own ``__init__``
    received ``many=`` and every read raised.
    """
    serializer_class = renderable_serializer_class(binding.output_serializer)
    if serializer_class is None:
        return raw
    return serializer_class(
        raw,
        many=binding.kind is SelectorKind.LIST,
        context=base_serializer_context(view=view, request=request),
    ).data


__all__ = ["render_drfs_resource_output"]
