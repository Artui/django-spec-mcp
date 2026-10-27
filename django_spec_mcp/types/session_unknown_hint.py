"""``SESSION_UNKNOWN_HINT`` - a session id arrived that this server will not honour — expired, evicted, terminated, or minted for a different principal."""

from __future__ import annotations

SESSION_UNKNOWN_HINT: str = "session-unknown"
"""A session id arrived that this server will not honour — expired, evicted,
terminated, or minted for a different principal. Deliberately one slug for all
four (paired with ``404``); the client's remedy is the same in every case:
re-``initialize``."""
