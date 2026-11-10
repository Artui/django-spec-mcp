from __future__ import annotations

from enum import Enum


class ToolContentKind(str, Enum):
    """What a tool's rendered payload becomes in the result's ``content`` array."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    RESOURCE_LINK = "resource_link"
