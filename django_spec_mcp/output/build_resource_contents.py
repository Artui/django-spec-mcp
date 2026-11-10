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
    """Encode a view resource's body into one ``resources/read`` block."""
    return encode_resource_contents(
        raw,
        binding=binding,
        uri=uri,
        producer="the selector",
    )
