from __future__ import annotations

_SESSION_ID_PREFIX_CHARS: int = 8


def session_fingerprint(session_id: str | None) -> str:
    """A short, non-replayable tag for correlating log lines about one client."""
    if not session_id:
        return "-"
    return f"{session_id[:_SESSION_ID_PREFIX_CHARS]}…"


__all__ = ["session_fingerprint"]
