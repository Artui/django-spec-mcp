from __future__ import annotations

SUBSCRIPTIONS_LISTEN_METHOD: str = "subscriptions/listen"
"""The method that opens a notification stream. Named here rather than inline
at the transport because it is the one method the view branches on before
dispatch."""
