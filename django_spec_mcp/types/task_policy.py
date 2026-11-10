from __future__ import annotations

from enum import Enum


class TaskPolicy(str, Enum):
    """Whether a binding may — or must — answer with a task handle."""

    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"
    REQUIRED = "required"
