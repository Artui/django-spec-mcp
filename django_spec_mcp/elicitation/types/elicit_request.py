from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.types.elicitation_create_method import ELICITATION_CREATE_METHOD


@dataclass(frozen=True)
class ElicitRequest:
    """One question to put to the user, in the spec's ``elicitation/create`` shape."""

    message: str
    """What to show the user: the service's own message, verbatim."""

    requested_schema: dict[str, Any]
    """The restricted JSON Schema describing the fields to collect. Built by
    ``build_requested_schema``,
    which also rejects a shape the client would have to refuse."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": ELICITATION_CREATE_METHOD,
            "params": {
                "mode": "form",
                "message": self.message,
                "requestedSchema": self.requested_schema,
            },
        }


__all__ = ["ElicitRequest"]
