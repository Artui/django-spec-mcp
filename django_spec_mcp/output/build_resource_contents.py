from __future__ import annotations

from typing import Any

from django_spec_mcp.output.encode_resource_contents import encode_resource_contents
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding


def build_resource_contents(
    *,
    binding: ResourceBinding,
    uri: str,
    raw: Any,
) -> ResourceContents | JsonRpcError:
    """Encode a resource's body into one ``resources/read`` block.

    Shared by both read handlers' plain path, a view's among them, and by
    ``read_drfs_resource`` and its async sibling, which render ``raw`` through
    the binding's ``output_serializer`` first
    (``render_drfs_resource_output``). The read paths are full parallel
    implementations rather than wrappers, so the encoding lives here, or the
    two transports drift and only one gets a fix.

    Encoded through ``encode_resource_contents``, the step a kernel resource's
    read shares: per the binding's
    [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding], ``JSON``
    pretty-prints, ``TEXT`` passes the value through verbatim and ``BLOB``
    base64-encodes it into the spec's ``blob`` field. A ``TEXT`` or ``BLOB``
    binding whose selector returned the wrong Python type comes back as a
    [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
    naming the selector, rather than as an exception."""
    return encode_resource_contents(
        raw,
        binding=binding,
        uri=uri,
        producer="the selector (after any output_serializer)",
    )
