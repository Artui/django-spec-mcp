from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django_spec_mcp.elicitation.types.elicit_request import ElicitRequest
from django_spec_mcp.types.result_type import ResultType


@dataclass(frozen=True)
class InputRequiredResult:
    """The answer to *"I cannot finish this without something you have"*."""

    input_requests: Mapping[str, ElicitRequest] = field(default_factory=dict)
    """Server-assigned key → the request the client must fulfil.

    **Never populated for a client that did not declare the matching
    capability**: the spec is explicit that a server *MUST NOT* send an
    ``elicitation/create`` to a client that did not declare ``elicitation``. The
    gate is
    ``can_ask_client``,
    consulted before this object is ever built."""

    request_state: str | None = None
    """Opaque to the client, signed by us. See
    ``django_spec_mcp.elicitation.sign_request_state``."""

    def __post_init__(self) -> None:
        if not self.input_requests and self.request_state is None:
            raise ValueError(
                "An InputRequiredResult must carry inputRequests, requestState, or both — "
                "one with neither tells the client nothing it can act on."
            )

    def to_dict(self) -> dict[str, Any]:
        """The wire object, ``resultType`` included."""
        out: dict[str, Any] = {"resultType": ResultType.INPUT_REQUIRED.value}
        if self.input_requests:
            out["inputRequests"] = {
                key: request.to_dict() for key, request in self.input_requests.items()
            }
        if self.request_state is not None:
            out["requestState"] = self.request_state
        return out


__all__ = ["InputRequiredResult"]
