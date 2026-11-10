from __future__ import annotations

from enum import Enum


class UIPermission(str, Enum):
    """A browser capability an interactive view asks the host to grant."""

    CAMERA = "camera"
    MICROPHONE = "microphone"
    GEOLOCATION = "geolocation"
    CLIPBOARD_WRITE = "clipboardWrite"
