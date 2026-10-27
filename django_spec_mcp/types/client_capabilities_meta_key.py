"""``CLIENT_CAPABILITIES_META_KEY`` - per-request _meta key declaring what the client supports."""

from __future__ import annotations

CLIENT_CAPABILITIES_META_KEY: str = "io.modelcontextprotocol/clientCapabilities"
"""Per-request ``_meta`` key declaring what the client supports.

Required on a modern request, and empty (``{}``) is a valid declaration. It
replaces the ``initialize`` handshake's one-time exchange: a server **MUST
NOT** rely on a capability the client did not declare *on that request*.
"""
