from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.protocol.types.server_capabilities import ServerCapabilities
from django_spec_mcp.types.server_info_meta_key import SERVER_INFO_META_KEY


@dataclass(frozen=True)
class DiscoverResult:
    """The server's response to a ``server/discover`` request."""

    supported_versions: tuple[str, ...]
    capabilities: ServerCapabilities
    server_info: Implementation
    instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "supportedVersions": list(self.supported_versions),
            "capabilities": self.capabilities.to_dict(),
            "_meta": {SERVER_INFO_META_KEY: self.server_info.to_dict()},
        }
        if self.instructions is not None:
            out["instructions"] = self.instructions
        return out


__all__ = ["DiscoverResult"]
