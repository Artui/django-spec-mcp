from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.types.client_capabilities_meta_key import CLIENT_CAPABILITIES_META_KEY
from django_spec_mcp.types.client_info_meta_key import CLIENT_INFO_META_KEY
from django_spec_mcp.types.protocol_version_meta_key import PROTOCOL_VERSION_META_KEY


@dataclass(frozen=True)
class RequestMetadata:
    """The per-request ``_meta`` a modern client sends on every request."""

    protocol_version: str
    client_capabilities: dict[str, Any] = field(default_factory=dict)
    client_info: Implementation | None = None

    @classmethod
    def from_params(cls, params: Any) -> RequestMetadata | None:
        """Read modern metadata off a request's ``params``, or ``None`` if legacy."""
        if not isinstance(params, dict):
            return None
        meta: Any = params.get("_meta")
        if not isinstance(meta, dict):
            return None
        version: Any = meta.get(PROTOCOL_VERSION_META_KEY)
        if not isinstance(version, str) or not version:
            return None

        capabilities: Any = meta.get(CLIENT_CAPABILITIES_META_KEY)
        info: Any = meta.get(CLIENT_INFO_META_KEY)
        return cls(
            protocol_version=version,
            client_capabilities=capabilities if isinstance(capabilities, dict) else {},
            client_info=_implementation(info),
        )


def _implementation(raw: Any) -> Implementation | None:
    """Project a ``clientInfo`` mapping, ignoring anything unusable."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        return None
    version: Any = raw.get("version")
    title: Any = raw.get("title")
    return Implementation(
        name=raw["name"],
        version=version if isinstance(version, str) else "",
        title=title if isinstance(title, str) else None,
    )


__all__ = ["RequestMetadata"]
