from __future__ import annotations

from typing import Any

from django_service_specs import SelectorKind
from rest_framework_services import base_serializer_context, renderable_serializer_class

from django_spec_mcp.output.encode_resource_contents import encode_resource_contents
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding


def build_resource_contents(
    *,
    binding: ResourceBinding,
    uri: str,
    raw: Any,
    view: Any = None,
    request: Any = None,
) -> ResourceContents | JsonRpcError:
    """Render a selector's return value into one ``resources/read`` block.

    Shared by the sync and async read handlers, which are full parallel
    implementations rather than wrappers — so anything that varies by binding
    lives here, or the two transports drift and only one gets a fix.

    Two steps, in order:

    1. **Render**, if the binding declares an ``output_serializer`` — with
       ``many=True`` for a ``LIST`` selector. The serializer gets DRF's baseline
       context (``request`` / ``format`` / ``view``) built from the synthesized
       ``view`` / ``request``, so a field reading ``self.context["request"]``
       resolves it as it would behind a view. A resource binding has no context
       provider of its own: its selector is a bare callable, not a spec.

       The declaration is resolved through drf-services'
       ``renderable_serializer_class`` before it is instantiated, as
       ``render_spec_output`` resolves it on the tool path. This site cannot
       delegate to that function — it takes a spec, and the binding holds none —
       so it makes the same resolution itself, rather than wrapping a dataclass
       here. A raw dataclass declared as the output therefore renders through a
       ``DataclassSerializer``; instantiated as declared, its own ``__init__``
       received ``many=`` and every read raised.
    2. **Encode**, through ``encode_resource_contents``, the step a kernel
       resource's read shares: per the binding's
       [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding], ``JSON``
       pretty-prints, ``TEXT`` passes the value through verbatim and ``BLOB``
       base64-encodes it into the spec's ``blob`` field. A ``TEXT`` or ``BLOB``
       binding whose selector returned the wrong Python type comes back as a
       [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
       naming the selector, rather than as an exception."""
    payload: Any = raw
    serializer_class = renderable_serializer_class(binding.output_serializer)
    if serializer_class is not None:
        payload = serializer_class(
            raw,
            many=binding.kind is SelectorKind.LIST,
            context=base_serializer_context(view=view, request=request),
        ).data

    return encode_resource_contents(
        payload,
        binding=binding,
        uri=uri,
        producer="the selector (after any output_serializer)",
    )


__all__ = ["build_resource_contents"]
