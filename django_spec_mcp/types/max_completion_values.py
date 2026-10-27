"""``MAX_COMPLETION_VALUES`` - the spec's hard cap on values in one completion/complete result."""

from __future__ import annotations

MAX_COMPLETION_VALUES: int = 100
"""The spec's hard cap on ``values`` in one ``completion/complete`` result. A
completer may return more; the handler slices to this and sets ``hasMore``."""
