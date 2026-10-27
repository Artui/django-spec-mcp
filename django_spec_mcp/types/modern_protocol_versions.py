"""``MODERN_PROTOCOL_VERSIONS`` - protocol revisions that carry version, identity and capabilities per request."""

from __future__ import annotations

MODERN_PROTOCOL_VERSIONS: frozenset[str] = frozenset({"2026-07-28"})
"""Protocol revisions that carry version, identity and capabilities per request.

The spec's own split: **modern** revisions (``2026-07-28`` and later) declare
everything on each request and hold no session; **legacy** ones
(``2025-11-25`` and earlier) negotiate once through ``initialize``. Every
era branch in this package reads from this set, so adding a revision is one
edit. Both eras are served concurrently on one endpoint, because legacy clients
have no fall-forward mechanism.
"""
