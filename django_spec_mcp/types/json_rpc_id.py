"""``JsonRpcId`` - type alias for the JSON-RPC id field."""

from __future__ import annotations

JsonRpcId = str | int | None
"""Type alias for the JSON-RPC ``id`` field. JSON-RPC 2.0 allows string,
integer or null IDs; notifications carry ``null``, requests carry a non-null ID
that clients correlate responses back by."""
