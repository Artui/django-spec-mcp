from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.protocol.types.icon import Icon


@dataclass(frozen=True)
class Implementation:
    """Identifies an MCP client or server: name + version, with an optional title."""

    name: str
    version: str
    title: str | None = None
    description: str | None = None
    website_url: str | None = None
    icons: tuple[Icon, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.website_url is not None:
            out["websiteUrl"] = self.website_url
        if self.icons:
            out["icons"] = [icon.to_dict() for icon in self.icons]
        return out


__all__ = ["Implementation"]
