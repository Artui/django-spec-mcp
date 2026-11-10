from __future__ import annotations

from typing import Any


def can_ask_client(client_capabilities: dict[str, Any]) -> bool:
    """Whether *this request* may be answered with a form to fill in."""
    elicitation: Any = client_capabilities.get("elicitation")
    if not isinstance(elicitation, dict):
        return False
    return not elicitation or "form" in elicitation


__all__ = ["can_ask_client"]
