"""``CLIENT_INFO_META_KEY`` - per-request _meta key carrying the client's self-reported identity."""

from __future__ import annotations

CLIENT_INFO_META_KEY: str = "io.modelcontextprotocol/clientInfo"
"""Per-request ``_meta`` key carrying the client's self-reported identity.
Optional and unverified; parsed for introspection and logging, and nothing
branches on it."""
