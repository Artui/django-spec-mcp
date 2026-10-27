"""``REQUEST_STATE_SALT`` - namespace for the HMAC over requestState."""

from __future__ import annotations

REQUEST_STATE_SALT: str = "django_spec_mcp.elicitation.request_state"
"""Namespace for the HMAC over ``requestState``.

A salt rather than a bare ``SECRET_KEY`` signature so a token minted here can
never verify against another of the project's signed values (password reset
links, session cookies, other ``django.core.signing`` callers) or vice versa."""
