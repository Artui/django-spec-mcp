from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Completion:
    """The ``completion`` object inside a ``completion/complete`` result."""

    values: tuple[str, ...] = ()
    total: int | None = None
    has_more: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"values": list(self.values), "hasMore": self.has_more}
        if self.total is not None:
            out["total"] = self.total
        return out


__all__ = ["Completion"]
