from __future__ import annotations

import base64
from typing import Any

from django_spec_mcp.output.encode_json import encode_json
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.resource_encoding import ResourceEncoding


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

    Encoded per the binding's
    [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding].
    ``JSON`` pretty-prints; ``TEXT`` passes the value through verbatim, which is what
    an HTML / Markdown / CSV resource needs; ``BLOB`` base64-encodes it into
    the spec's ``blob`` field. ``text`` and ``blob`` are mutually exclusive
    on a ``contents`` entry, so exactly one is ever set.

    A ``TEXT`` or ``BLOB`` binding whose selector returned the wrong Python type can
    only surface at read time, so it comes back as a
    [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
    rather than an exception — a well-formed error response instead of a 500."""
    payload: Any = raw
    if binding.encoding is ResourceEncoding.BLOB:
        if not isinstance(payload, bytes | bytearray | memoryview):
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Resource {binding.name!r} declares encoding=BLOB but produced "
                f"{type(payload).__name__}, not bytes. A BLOB resource's body is "
                "the binary itself, base64-encoded here, so the selector (after "
                "any output_serializer) must return the bytes.",
            )
        return ResourceContents(
            uri=uri,
            mime_type=binding.mime_type,
            blob=base64.b64encode(payload).decode("ascii"),
            meta=dict(binding.meta) or None,
        )

    text: str
    if binding.encoding is ResourceEncoding.TEXT:
        if not isinstance(payload, str):
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Resource {binding.name!r} declares encoding=TEXT but produced "
                f"{type(payload).__name__}, not str. A TEXT resource's body is "
                "returned verbatim, so the selector (after any output_serializer) "
                "must return the document itself.",
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


__all__ = ["build_resource_contents"]
