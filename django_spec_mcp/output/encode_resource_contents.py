from __future__ import annotations

import base64
from typing import Any

from django_spec_mcp.output.encode_json import encode_json
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.resource_encoding import ResourceEncoding


def encode_resource_contents(
    payload: Any,
    *,
    binding: ResourceBinding,
    uri: str,
    producer: str,
) -> ResourceContents | JsonRpcError:
    """Encode a resource's rendered ``payload`` into its one ``resources/read`` block."""
    if binding.encoding is ResourceEncoding.BLOB:
        if not isinstance(payload, bytes | bytearray | memoryview):
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Resource {binding.name!r} declares encoding=BLOB but produced "
                f"{type(payload).__name__}, not bytes. A BLOB resource's body is "
                f"the binary itself, base64-encoded here, so {producer} must return "
                "the bytes.",
            )
        return ResourceContents(
            uri=uri,
            mime_type=binding.mime_type,
            blob=base64.b64encode(bytes(payload)).decode("ascii"),
            meta=dict(binding.meta) or None,
        )

    text: str
    if binding.encoding is ResourceEncoding.TEXT:
        if not isinstance(payload, str):
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Resource {binding.name!r} declares encoding=TEXT but produced "
                f"{type(payload).__name__}, not str. A TEXT resource's body is "
                f"returned verbatim, so {producer} must return the document itself.",
            )
        text = payload
    else:
        text = encode_json(payload)

    return ResourceContents(
        uri=uri,
        mime_type=binding.mime_type,
        text=text,
        meta=dict(binding.meta) or None,
    )


__all__ = ["encode_resource_contents"]
