"""``UIPermission`` - a browser capability an interactive view asks the host to grant."""

from __future__ import annotations

from enum import Enum


class UIPermission(str, Enum):
    """A browser capability an interactive view asks the host to grant.

    The host decides; this only declares what the view would use, in the
    resource's ``_meta.ui.permissions``. Anything not declared is denied by the
    iframe sandbox the host builds.
    """

    CAMERA = "camera"
    MICROPHONE = "microphone"
    GEOLOCATION = "geolocation"
    CLIPBOARD_WRITE = "clipboardWrite"
