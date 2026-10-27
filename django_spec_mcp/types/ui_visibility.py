"""``UIVisibility`` - who may call a tool that is linked to an interactive view."""

from __future__ import annotations

from enum import Enum


class UIVisibility(str, Enum):
    """Who may call a tool that is linked to an interactive view.

    Declared per tool in ``_meta.ui.visibility`` and **enforced by the host**,
    which the spec requires not to offer the model a tool whose visibility
    omits ``MODEL``. This server only declares it — nothing here filters
    ``tools/list`` on it, because a client that does not implement the
    extension would not honour the rule anyway.

    Attributes:
        MODEL: The agent may call it — ordinary tool behaviour.
        APP: The view may call it. An ``APP``-only tool is a fine-grained
            operation that serves the view rather than the conversation.
    """

    MODEL = "model"
    APP = "app"
