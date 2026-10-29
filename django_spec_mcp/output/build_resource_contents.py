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
    """Encode a view resource's body into one ``resources/read`` block.

    Shared by both read handlers' view path. The two handlers are full
    parallel implementations rather than wrappers, so the call lives here, or
    the sync and async reads drift and only one gets a fix.

    The encoding, by the binding's
    [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding], is
    ``encode_resource_contents``'s, the step a kernel resource's read shares; a
    value of the wrong Python type comes back as a
    [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
    naming the selector, rather than as an exception."""
    return encode_resource_contents(
        raw,
        binding=binding,
        uri=uri,
        producer="the selector",
    )
