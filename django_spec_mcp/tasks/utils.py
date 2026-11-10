from __future__ import annotations

import re
import secrets

from django.utils import timezone

from django_spec_mcp.types.elicitation_key import ELICITATION_KEY

# The spec requires ids a third party cannot enumerate or guess, and that is
# load-bearing here in a way it is not for a session: with no ``tasks/list`` and
# no session to scope a lookup by, unguessability *is* the containment boundary
# — ownership is checked on top of it, not instead of it.
_TASK_ID_BYTES: int = 32


# The alphabet ``token_urlsafe`` draws from, plus a length ceiling: this is the
# shape of an id this package hands out, and the only shape a store here has to
# be able to hold. The ceiling is generous enough for any id shape a custom
# store might mint (a UUID, a ULID) and far below the 250-byte key limit
# memcached enforces.
_TASK_ID_SHAPE = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def new_task_id() -> str:
    return secrets.token_urlsafe(_TASK_ID_BYTES)


def is_wellformed_task_id(task_id: str) -> bool:
    """Whether ``task_id`` could be an id this package issued."""
    return bool(_TASK_ID_SHAPE.match(task_id))


def input_request_key(round_: int) -> str:
    """The ``inputRequests`` key of a task's ``round_``-th question (from 1)."""
    return f"{ELICITATION_KEY}-{round_}"


def now_iso() -> str:
    """Current time as the ISO 8601 string the wire format wants."""
    return timezone.now().isoformat()


__all__ = ["input_request_key", "is_wellformed_task_id", "new_task_id", "now_iso"]
