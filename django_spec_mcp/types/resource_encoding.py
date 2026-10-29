"""``ResourceEncoding`` - how a resource's selector return value becomes the resources/read body."""

from __future__ import annotations

from enum import Enum


class ResourceEncoding(str, Enum):
    """How a resource's rendered value becomes the ``resources/read`` body.

    The rendered value is what the spec's presenter returns (its selector's
    value, when it declares no presenter), and a view's selector's return as
    it is. Declared separately from the binding's
    ``mimeType`` rather than sniffed from it — sniffing would silently change
    behaviour for anyone already advertising a non-JSON type.

    On the kernel form, ``TEXT`` and ``BLOB`` are refused at registration
    beside a spec declaring ``affordances`` or ``allow_none``: the first makes
    the presented value an object and the second makes a missing row ``None``,
    and neither is a document or bytes.

    Attributes:
        JSON: Pretty-print the value as JSON. The default, and what every
            data resource wants.
        TEXT: The value is already the body, and must be a ``str``. For HTML,
            Markdown, CSV or plain text, where JSON-encoding would wrap the
            payload in a quoted string literal.
        BLOB: The value is binary, ``bytes``, ``bytearray`` or ``memoryview``,
            and the body is base64-encoded into the spec's ``blob`` field
            instead of ``text`` — the two are mutually exclusive on a
            ``contents`` entry.
    """

    JSON = "json"
    TEXT = "text"
    BLOB = "blob"
