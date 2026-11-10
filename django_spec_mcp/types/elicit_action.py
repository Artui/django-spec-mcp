from __future__ import annotations

from enum import Enum


class ElicitAction(str, Enum):
    """What the user did with the form, per ``ElicitResult.action``."""

    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"
