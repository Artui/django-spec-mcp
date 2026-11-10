from __future__ import annotations

from enum import Enum


class CacheScope(str, Enum):
    """How widely a cacheable result may be reused, per ``Cache-Control``."""

    PUBLIC = "public"
    PRIVATE = "private"
