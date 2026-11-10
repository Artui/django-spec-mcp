from __future__ import annotations

from enum import Enum


class UIVisibility(str, Enum):
    """Who may call a tool that is linked to an interactive view."""

    MODEL = "model"
    APP = "app"
