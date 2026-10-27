"""``PROTOCOL_VERSION_META_KEY`` - per-request _meta key naming the revision a modern request speaks."""

from __future__ import annotations

PROTOCOL_VERSION_META_KEY: str = "io.modelcontextprotocol/protocolVersion"
"""Per-request ``_meta`` key naming the revision a modern request speaks.

**This is the era discriminator** — not the ``MCP-Protocol-Version`` header,
which legacy clients have sent since ``2025-06-18``, and not the method, since
most methods exist in both eras.
"""
