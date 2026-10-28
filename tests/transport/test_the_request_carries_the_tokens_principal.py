"""From the moment a request authenticates, its ``user`` is the token's principal.

The project's ``AuthenticationMiddleware`` resolves ``request.user`` from the
session cookie, and the cookie may name someone other than the bearer token's
principal. An ``MCPPermission`` is handed the transport's request before any
handler runs (a prompt's gate, a streamed tool's pre-flight), so whatever the
transport leaves on it is who that permission judges. Each case here logs one
user in through a real session and authenticates as another with a bearer
token, and asks what the permission saw.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

_MODERN = "2026-07-28"

# Signed-cookie sessions, because the suite installs no sessions app.
_SESSION_SETTINGS: dict[str, Any] = {
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
    "MIDDLEWARE": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ],
}


class _BearerBackend(AllowAnyBackend):
    """``Authorization: Bearer <username>`` names the caller; no header, nobody."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        header: str = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return TokenInfo(user=None, scopes=(), audience=None, raw=None)
        user = get_user_model().objects.get(username=header.removeprefix("Bearer "))
        return TokenInfo(user=user, scopes=(), audience=None, raw=header)


class _Recording:
    """An ``MCPPermission`` that admits every call and keeps the request it judged."""

    def __init__(self) -> None:
        self.seen: list[tuple[HttpRequest, TokenInfo]] = []

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        self.seen.append((request, token))
        return True

    def required_scopes(self) -> list[str]:
        return []


def _server(recording: _Recording) -> MCPServer:
    server = MCPServer(
        name="principal",
        auth_backend=_BearerBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_prompt(name="greet", render=lambda: "hello", permissions=[recording])
    binding = server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=[Unrestricted()]),
        name="ping",
    )
    # A kernel binding's ``permissions`` are only its ``scopes=`` gate, so the
    # recorder stands in for that gate to be what the streamed pre-flight asks.
    object.__setattr__(binding, "permissions", (recording,))
    return server


def _body(method: str, name: str, *, progress: bool = False) -> tuple[str, dict[str, str]]:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": _MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    if progress:
        meta["progressToken"] = "p1"
    params: dict[str, Any] = {"name": name, "_meta": meta}
    if method == "tools/call":
        params["arguments"] = {}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return body, {"Mcp-Protocol-Version": _MODERN, "Mcp-Method": method, "Mcp-Name": name}


def _sync_call(session_user: Any, bearer: str | None, method: str, name: str) -> Any:
    client = Client()
    client.force_login(session_user)
    body, headers = _body(method, name)
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    return client.post("/mcp/", body, content_type="application/json", headers=headers)


async def _async_call(
    session_user: Any, bearer: str | None, method: str, name: str, *, progress: bool = False
) -> Any:
    client = AsyncClient()
    await client.aforce_login(session_user)
    body, headers = _body(method, name, progress=progress)
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    response = await client.post("/mcp/", body, content_type="application/json", headers=headers)
    if response.streaming:
        # Drained so the stream's task finishes inside the test.
        b"".join([chunk async for chunk in response.streaming_content])
    return response


async def _call(route: str, recording: _Recording, session_user: Any, bearer: str | None) -> Any:
    """Send one call on ``route`` with the session cookie and bearer given."""
    is_async: bool = route != "sync prompt"
    with override_settings(
        ROOT_URLCONF=urlconf_for(_server(recording), is_async=is_async), **_SESSION_SETTINGS
    ):
        if route == "sync prompt":
            return await sync_to_async(_sync_call)(session_user, bearer, "prompts/get", "greet")
        if route == "async prompt":
            return await _async_call(session_user, bearer, "prompts/get", "greet")
        return await _async_call(session_user, bearer, "tools/call", "ping", progress=True)


_ROUTES = ["sync prompt", "async prompt", "streamed tool pre-flight"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_permission_sees_the_tokens_principal_not_the_sessions(route: str) -> None:
    users = get_user_model().objects
    session_user = await users.acreate(username="cookie-holder")
    token_user = await users.acreate(username="token-holder")
    recording = _Recording()

    response = await _call(route, recording, session_user, "token-holder")

    assert response.status_code == 200, getattr(response, "content", response)
    if route == "streamed tool pre-flight":
        # Streamed, so the first ask was the pre-flight's, ahead of the handler.
        assert response["Content-Type"].startswith("text/event-stream")
    assert recording.seen, "the permission was never asked"
    request, token = recording.seen[0]
    assert request.user == token_user
    assert await request.auser() == token_user
    assert request.mcp_token is token


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_token_naming_no_one_leaves_an_anonymous_user_not_the_sessions(
    route: str,
) -> None:
    session_user = await get_user_model().objects.acreate(username="cookie-holder")
    recording = _Recording()

    response = await _call(route, recording, session_user, None)

    assert response.status_code == 200, getattr(response, "content", response)
    request, _ = recording.seen[0]
    assert isinstance(request.user, AnonymousUser)
    assert isinstance(await request.auser(), AnonymousUser)
