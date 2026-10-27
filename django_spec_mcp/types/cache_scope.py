"""``CacheScope`` - how widely a cacheable result may be reused, per Cache-Control."""

from __future__ import annotations

from enum import Enum


class CacheScope(str, Enum):
    """How widely a cacheable result may be reused, per ``Cache-Control``.

    **Derived, never configured.** ``PUBLIC`` licenses any intermediary to
    serve the response *across authorization contexts*, so a result that varies
    by caller and is labelled ``PUBLIC`` is a cross-tenant disclosure with a
    cache in front of it — precisely the mistake a settings knob would invite.
    The handlers work it out from what shaped the response: a
    permission-filtered listing is ``PRIVATE``, and so is a ``tools/list`` that
    asked an operation-scope affordance of this caller; any other listing is
    ``PUBLIC``, and a resource body always ``PRIVATE``.
    """

    PUBLIC = "public"
    PRIVATE = "private"
