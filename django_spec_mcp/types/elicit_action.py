"""``ElicitAction`` - what the user did with the form, per ElicitResult.action."""

from __future__ import annotations

from enum import Enum


class ElicitAction(str, Enum):
    """What the user did with the form, per ``ElicitResult.action``.

    The three are not interchangeable and this package does not collapse them:
    ``DECLINE`` is a decision, ``CANCEL`` is the absence of one. Both stop the
    call, but a client — or a model reading the error — can tell "the user said
    no" from "the user closed the dialog", and only the second is worth
    retrying.
    """

    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"
