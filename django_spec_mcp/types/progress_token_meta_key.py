"""``PROGRESS_TOKEN_META_KEY`` - per-request _meta key by which a client asks to be told about progress."""

from __future__ import annotations

PROGRESS_TOKEN_META_KEY: str = "progressToken"
"""Per-request ``_meta`` key by which a client asks to be told about progress.

Unprefixed and identical in both eras, so progress needs no era branch. Its
presence is the *only* signal that streaming is wanted: a server MAY decline to
notify at all, so a request without the token is answered with a single JSON
object rather than a one-event stream.
"""
