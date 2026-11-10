from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UICsp:
    """The network origins an interactive view needs, declared to the host."""

    connect_domains: Sequence[str] = ()
    resource_domains: Sequence[str] = ()
    frame_domains: Sequence[str] = ()
    base_uri_domains: Sequence[str] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the extension's camelCase wire shape, omitting empties."""
        out: dict[str, Any] = {}
        for key, values in (
            ("connectDomains", self.connect_domains),
            ("resourceDomains", self.resource_domains),
            ("frameDomains", self.frame_domains),
            ("baseUriDomains", self.base_uri_domains),
        ):
            if values:
                out[key] = list(values)
        return out


__all__ = ["UICsp"]
