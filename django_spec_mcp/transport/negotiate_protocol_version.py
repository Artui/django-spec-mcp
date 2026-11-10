from __future__ import annotations

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.transport.protocol_version import resolve_protocol_version


def negotiate_protocol_version(
    header_value: str | None, *, is_sessionless: bool, config: MCPConfig
) -> str | None:
    """Pick the protocol version to associate with a request, or ``None`` to reject."""
    supported: tuple[str, ...] = config.legacy_protocol_versions
    resolved: str | None = resolve_protocol_version(header_value, supported)
    if resolved is not None:
        return resolved
    if header_value and header_value not in config.protocol_versions:
        return None
    fallback: str | None = config.legacy_fallback_version
    if is_sessionless:
        return fallback if fallback is not None else config.protocol_versions[0]
    if not header_value and not config.require_protocol_version_header:
        return fallback
    return None


__all__ = ["negotiate_protocol_version"]
