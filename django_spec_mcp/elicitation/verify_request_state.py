from __future__ import annotations

from typing import Any

from django.core import signing

from django_spec_mcp.elicitation.types.request_state import RequestState
from django_spec_mcp.types.request_state_salt import REQUEST_STATE_SALT


def verify_request_state(
    raw: Any,
    *,
    principal: str,
    fingerprint: str,
    max_age: int,
) -> RequestState | None:
    """Unpack a client-presented ``requestState``, or ``None`` if it is not ours."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        payload: Any = signing.loads(raw, salt=REQUEST_STATE_SALT, max_age=max_age)
    except signing.BadSignature:
        return None

    state: RequestState | None = RequestState.from_payload(payload)
    if state is None:
        return None
    if state.principal != principal or state.fingerprint != fingerprint:
        return None
    return state


__all__ = ["verify_request_state"]
