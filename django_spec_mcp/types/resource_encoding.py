from __future__ import annotations

from enum import Enum


class ResourceEncoding(str, Enum):
    """How a resource's rendered value becomes the ``resources/read`` body."""

    JSON = "json"
    TEXT = "text"
    BLOB = "blob"
