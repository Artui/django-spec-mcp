"""``ResourceEncoding`` - how a resource's selector return value becomes the resources/read body."""

from __future__ import annotations

from enum import Enum


class ResourceEncoding(str, Enum):
    """How a resource's selector return value becomes the ``resources/read`` body.

    Declared separately from the binding's ``mimeType`` rather than sniffed
    from it — sniffing would silently change behaviour for anyone already
    advertising a non-JSON type.

    Attributes:
        JSON: Pretty-print the value as JSON. The default, and what every
            selector-backed data resource wants.
        TEXT: The value is already the body; the selector must return a
            ``str``. For HTML, Markdown, CSV or plain text, where
            JSON-encoding would wrap the payload in a quoted string literal.
        BLOB: The value is binary. The selector returns ``bytes`` and the body
            is base64-encoded into the spec's ``blob`` field instead of
            ``text`` — the two are mutually exclusive on a ``contents`` entry.
    """

    JSON = "json"
    TEXT = "text"
    BLOB = "blob"
