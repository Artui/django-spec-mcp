"""``SUBSCRIPTIONS_LISTEN_METHOD`` - the method that opens a notification stream."""

from __future__ import annotations

SUBSCRIPTIONS_LISTEN_METHOD: str = "subscriptions/listen"
"""The method that opens a notification stream. Named here rather than inline
at the transport because it is the one method the viewset branches on before
dispatch."""
