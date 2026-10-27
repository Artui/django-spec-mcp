"""``ToolContentKind`` - what a tool's rendered payload becomes in the result's content array."""

from __future__ import annotations

from enum import Enum


class ToolContentKind(str, Enum):
    """What a tool's rendered payload becomes in the result's ``content`` array.

    Declared per binding rather than sniffed from the payload, for the same reason as
    [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding]: a base64
    ``str`` and a text body are indistinguishable by inspection.

    Embedded (``resource``) blocks have no kind here on purpose — inlining contents
    means producing them, which ``resources/read`` already does, so a tool returns a
    ``RESOURCE_LINK`` and lets the client decide whether to spend context on the body.
    [`ToolContentBlock.embedded_resource`][django_spec_mcp.protocol.types.tool_content_block.ToolContentBlock.embedded_resource]
    remains available for a caller building blocks by hand.

    Attributes:
        TEXT: One text block rendered per the binding's ``OutputFormat``,
            mirrored in ``structuredContent``. The default.
        IMAGE: The payload is the media itself — ``bytes``, or a ``str``
            already in base64. Carries **no** ``structuredContent``: binary is
            not JSON, and an ``outputSchema`` over it would describe a shape
            that never arrives.
        AUDIO: As ``IMAGE``.
        RESOURCE_LINK: The payload names resources rather than containing them
            — one mapping with ``uri`` / ``name`` (plus optional
            ``description`` / ``mimeType``), or a list of them.
            ``structuredContent`` is kept, since the links *are* JSON.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    RESOURCE_LINK = "resource_link"
