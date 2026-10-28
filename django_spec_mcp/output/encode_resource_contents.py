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
    """Encode a resource's rendered ``payload`` into its one ``resources/read`` block.

    The encode step both reads share: ``build_resource_contents`` calls it on
    what the drf-services read's serializer rendered, and
    ``kernel_utils.resource_read_result`` on what a kernel spec's presenter
    presented. One function, so an encoding changes for both reads at once.

    Per the binding's [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding]:
    ``JSON`` pretty-prints ``payload``; ``TEXT`` serves it verbatim, which is
    what an HTML, Markdown or CSV resource needs; ``BLOB`` base64-encodes it into
    the spec's ``blob`` field. ``text`` and ``blob`` are mutually exclusive on a
    ``contents`` entry, so exactly one is ever set. The block carries the
    binding's ``mime_type`` as registered, never inferred from the encoding or
    checked against it, and its ``_meta`` when it has one.

    A ``TEXT`` payload that is not a ``str``, or a ``BLOB`` one that is not
    bytes-like, can only surface at read time, so it comes back as a
    [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
    rather than an exception: a well-formed error response instead of a 500.
    ``producer`` names what had to return the right type on the caller's path,
    so the message sends its reader to the code that produced the value.
    """
    if binding.encoding is ResourceEncoding.BLOB:
        # One branch arc for the three types, so each is held by its own case of
        # test_bytes_like_are_base64_in_blob_with_no_text.
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
            # ``bytes()`` first: a strided ``memoryview`` is bytes-like but not
            # a buffer ``b64encode`` can read in place
            # (test_a_non_contiguous_memoryview_is_encoded_as_its_bytes).
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
