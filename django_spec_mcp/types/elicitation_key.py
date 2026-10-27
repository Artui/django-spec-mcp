"""``ELICITATION_KEY`` - the key this package files its one question under in inputRequests."""

from __future__ import annotations

ELICITATION_KEY: str = "additionalInput"
"""The key this package files its one question under in ``inputRequests``.
Keys are server-assigned and need only be unique within a single result; one
``AdditionalInputRequired`` asks one question, so a fixed name is enough."""
