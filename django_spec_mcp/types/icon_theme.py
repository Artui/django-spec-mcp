from __future__ import annotations

from enum import Enum


class IconTheme(str, Enum):
    """Which background an [`Icon`][django_spec_mcp.protocol.types.icon.Icon] was designed for."""

    LIGHT = "light"
    DARK = "dark"
