"""The dataclass every job surface in this example presents a row through."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class JobOutput:
    """A job as a client sees it. ``id`` is the UUID's string form."""

    id: str
    status: str
    duration_seconds: int
    error: str
    created_at: dt.datetime
    finished_at: dt.datetime | None
