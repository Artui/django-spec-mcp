from __future__ import annotations

from django.core import signing

from django_spec_mcp.elicitation.types.request_state import RequestState
from django_spec_mcp.types.request_state_salt import REQUEST_STATE_SALT


def sign_request_state(state: RequestState) -> str:
    """Serialise and HMAC a ``RequestState`` into the wire ``requestState``."""
    return signing.dumps(state.to_payload(), salt=REQUEST_STATE_SALT, compress=True)


__all__ = ["sign_request_state"]
