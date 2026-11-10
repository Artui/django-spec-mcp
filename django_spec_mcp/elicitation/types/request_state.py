from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequestState:
    """What the server needs back to carry on, and what it checks before it does."""

    principal: str
    """Who the state was minted for. Rejecting a mismatch stops one caller
    redeeming another's state — the same derived principal id task ownership
    uses, so a leaked token and a leaked ``requestState`` fail the same way."""

    fingerprint: str
    """A digest of the call the state belongs to — method, tool name and the
    arguments *as the client sent them*, never the merged ones. That is what
    keeps it stable across rounds: the client retries the original call
    unchanged while our copy grows an answer each round, so binding to the
    merged form would invalidate the state on the first retry."""

    answers: dict[str, Any] = field(default_factory=dict)
    """Every accepted answer so far, keyed by the input name the service asked
    for it under.

    **This is why more than one round works.** A retry carries only the *latest*
    round's ``inputResponses``, so a service asking a second question would
    otherwise lose the first answer and ask for it forever. Accumulating here
    rather than in a store keyed by some id keeps the server stateless."""

    round: int = 0
    """How many questions have already been answered on this call. Bounded by
    ``MAX_INPUT_ROUNDS`` so a service that can never be satisfied stops the
    exchange."""

    def to_payload(self) -> dict[str, Any]:
        """The JSON-serialisable form that gets signed."""
        return {
            "p": self.principal,
            "f": self.fingerprint,
            "a": self.answers,
            "r": self.round,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> RequestState | None:
        """Rebuild from a verified payload, or ``None`` if it is not one."""
        if not isinstance(payload, dict):
            return None
        principal: Any = payload.get("p")
        fingerprint: Any = payload.get("f")
        answers: Any = payload.get("a")
        round_: Any = payload.get("r")
        if not isinstance(principal, str) or not isinstance(fingerprint, str):
            return None
        if not isinstance(answers, dict) or not isinstance(round_, int):
            return None
        return cls(principal=principal, fingerprint=fingerprint, answers=answers, round=round_)


__all__ = ["RequestState"]
