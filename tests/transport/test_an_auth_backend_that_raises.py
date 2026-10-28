"""An exception an auth backend raises is answered by Django, not mapped here.

The endpoints call ``authenticate`` outside their dispatch backstop, so what a
backend raises reaches Django's own handler: its ``PermissionDenied`` and
``Http404`` are its HTML ``403`` and ``404`` pages, and anything else is a
``500``. A backend answers a caller it does not admit by returning ``None``,
which is the ``401`` with the challenge.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest
from django.test import AsyncClient, Client, override_settings

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "raising-backend", "version": "1"},
        },
    }
)


class _AuthenticationFailed(Exception):
    """Stands in for any exception a backend's own library raises."""


class _RaisingBackend(AllowAnyBackend):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        raise self._exc


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (PermissionDenied("no"), 403),
        (Http404("no"), 404),
        (_AuthenticationFailed("no"), 500),
    ],
    ids=["permission-denied", "http404", "other"],
)
async def test_django_answers_what_an_auth_backend_raises(
    is_async: bool, exc: Exception, status: int
) -> None:
    server = MCPServer(
        name="raising",
        auth_backend=_RaisingBackend(exc),
        session_store=InMemorySessionStore(),
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        if is_async:
            response = await AsyncClient(raise_request_exception=False).post(
                "/mcp/", _INITIALIZE, content_type="application/json"
            )
        else:
            response = await sync_to_async(Client(raise_request_exception=False).post)(
                "/mcp/", _INITIALIZE, content_type="application/json"
            )
    assert response.status_code == status, response.content[:200]
    assert response["Content-Type"].startswith("text/html")
    assert "WWW-Authenticate" not in response
