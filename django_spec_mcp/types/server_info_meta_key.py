"""``SERVER_INFO_META_KEY`` - reserved _meta key carrying the server's self-reported identity."""

from __future__ import annotations

SERVER_INFO_META_KEY: str = "io.modelcontextprotocol/serverInfo"
"""Reserved ``_meta`` key carrying the server's self-reported identity.

Where ``server/discover`` puts what ``initialize`` returned as a top-level
``serverInfo``. Unverified, and clients are told not to make security decisions
from it — hence the metadata namespace rather than negotiated protocol state.
"""
