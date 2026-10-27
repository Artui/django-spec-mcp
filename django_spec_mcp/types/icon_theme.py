"""``IconTheme`` - which background an [`Icon`][django_spec_mcp.protocol.types.icon.Icon] was designed for."""

from __future__ import annotations

from enum import Enum


class IconTheme(str, Enum):
    """Which background an [`Icon`][django_spec_mcp.protocol.types.icon.Icon]
    was designed for.

    Omitting the theme (``None``) tells the client the icon works on either,
    which is right for most artwork. Declare it only when shipping a
    light/dark pair.
    """

    LIGHT = "light"
    DARK = "dark"
