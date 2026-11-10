from __future__ import annotations

from django.http import HttpResponse, JsonResponse


def build_unauthenticated_response(challenge: str) -> HttpResponse:
    """Build a spec-compliant 401 response with the supplied ``WWW-Authenticate`` value."""
    response: HttpResponse = JsonResponse(
        {"error": "unauthorized", "error_description": "Authentication required."},
        status=401,
    )
    response["WWW-Authenticate"] = challenge
    return response


__all__ = ["build_unauthenticated_response"]
