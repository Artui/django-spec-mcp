from __future__ import annotations

from enum import Enum


class ToolContentKind(str, Enum):
    """What a tool's rendered payload becomes in the result's ``content`` array.

    Declared per binding rather than sniffed from the payload, for the same reason as
    [`ResourceEncoding`][django_spec_mcp.types.ResourceEncoding]: a base64
    ``str`` and a text body are indistinguishable by inspection.

    A file is declared on the spec, never here: a spec presenting through the
    kernel's ``FilePresenter`` takes ``IMAGE``, ``AUDIO`` or ``RESOURCE`` by its
    content type, derived at registration, and any other declaration beside it is
    refused. A media or ``RESOURCE`` kind over a spec presenting no file is refused
    too. A row that already has a URI is still better linked (``RESOURCE_LINK``),
    so the client decides whether to spend context on the body; a file a service
    generates has no resource to link to, so it is embedded.

    Attributes:
        TEXT: One text block carrying the payload as JSON, mirrored in
            ``structuredContent``. The default.
        IMAGE: The payload is the media itself — ``bytes``, or a ``str``
            already in base64. Carries **no** ``structuredContent``: binary is
            not JSON, and an ``outputSchema`` over it would describe a shape
            that never arrives.
        AUDIO: As ``IMAGE``.
        RESOURCE: A ``ServiceSpec``'s file whose type is neither ``image/*``
            nor ``audio/*``, embedded as a ``resource`` block carrying the blob
            under a ``spec-mcp://files/<filename>`` URI that names it and is not
            readable. No ``structuredContent``, as ``IMAGE``. A ``SelectorSpec``
            reads a row that can be a resource, so its non-media file is refused
            as a tool: register it with ``register_resource``.
        RESOURCE_LINK: The payload names resources rather than containing them
            — one mapping with ``uri`` / ``name`` (plus optional
            ``description`` / ``mimeType``), or a list of them.
            ``structuredContent`` is kept, since the links *are* JSON.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    RESOURCE_LINK = "resource_link"
    RESOURCE = "resource"
