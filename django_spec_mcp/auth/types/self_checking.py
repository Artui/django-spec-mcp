from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SelfChecking(Protocol):
    """An auth backend that can say whether it is able to serve requests."""

    def check_configuration(self) -> None: ...


__all__ = ["SelfChecking"]
