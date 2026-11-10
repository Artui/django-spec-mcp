from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.prompt_registry import PromptRegistry
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.transport.async_streamable_http_view import (
    AsyncStreamableHttpView,
)
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from django_spec_mcp.transport.streamable_http_view import (
    StreamableHttpView,
)

factory = RequestFactory()
_LEGACY_VERSION = "2025-11-25"
_MODERN_VERSION = "2026-07-28"


class _HeaderPrincipalBackend:
    """Authenticates on a header, so 'no credential' is expressible."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        principal: str | None = request.headers.get("X-Principal")
        if principal is None:
            return None
        return TokenInfo(user=SimpleNamespace(pk=principal))

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(self, *, scopes: Any = None, error: Any = None) -> str:
        del scopes, error
        return (
            'Bearer realm="mcp", '
            'resource_metadata="https://example.test/.well-known/oauth-protected-resource", '
            'error="invalid_token"'
        )


def _view(*, sessions: bool, is_async: bool = False, broker: Any = None) -> Any:
    common: dict[str, Any] = {
        "tools": ToolRegistry(),
        "resources": ResourceRegistry(),
        "prompts": PromptRegistry(),
        "auth_backend": _HeaderPrincipalBackend(),
        "session_store": InMemorySessionStore(),
        "config": build_mcp_config(sessions_enabled=sessions),
    }
    if is_async:
        return AsyncStreamableHttpView.as_view(sse_broker=broker, **common)
    return StreamableHttpView.as_view(**common)


def _get(*, version: str = _LEGACY_VERSION, principal: str | None = None) -> Any:
    headers: dict[str, str] = {"Mcp-Protocol-Version": version}
    if principal is not None:
        headers["X-Principal"] = principal
    return factory.get("/mcp/", headers=headers)


# ---------- the sync transport: no stream on any configuration ----------


@pytest.mark.parametrize("sessions", [True, False])
def test_sync_get_is_405_without_a_credential(sessions: bool) -> None:
    """WSGI implements no GET stream at all, so the setting cannot matter."""
    response = _view(sessions=sessions)(_get())
    assert response.status_code == 405
    assert "WWW-Authenticate" not in response


@pytest.mark.parametrize("sessions", [True, False])
def test_sync_get_answers_the_same_with_a_credential(sessions: bool) -> None:
    """The credential never matters here."""
    assert _view(sessions=sessions)(_get(principal="alice")).status_code == 405


def test_sync_get_is_405_for_a_modern_caller_too() -> None:
    """A modern caller gets the same ``405``, not a challenge."""
    assert _view(sessions=True)(_get(version=_MODERN_VERSION)).status_code == 405


# ---------- the async transport: 405 only where there is no stream ----------


async def test_async_get_is_405_without_a_credential_when_sessionless() -> None:
    """A session id *is* the channel address, so sessionless has none to open."""
    view = _view(sessions=False, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get())
    assert response.status_code == 405
    assert "WWW-Authenticate" not in response


async def test_async_get_is_405_without_a_credential_when_no_broker_is_wired() -> None:
    """Holds the ``broker is None`` conjunct of the guard on its own."""
    view = _view(sessions=True, is_async=True, broker=None)
    response = await view(_get())
    assert response.status_code == 405
    assert "WWW-Authenticate" not in response


async def test_async_get_is_405_without_a_credential_when_sessions_are_off() -> None:
    """Holds the ``not sessions_enabled`` conjunct on its own."""
    view = _view(sessions=False, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get())
    assert response.status_code == 405


@pytest.mark.parametrize("sessions,broker", [(False, True), (True, False), (False, False)])
async def test_async_get_answers_the_same_with_a_credential(sessions: bool, broker: bool) -> None:
    """No credential moves any of these configurations off ``405``."""
    view = _view(sessions=sessions, is_async=True, broker=InMemorySSEBroker() if broker else None)
    assert (await view(_get(principal="alice"))).status_code == 405


# ---------- and 401 stays exactly where a token does decide the outcome ----------


async def test_async_get_still_challenges_when_a_stream_is_actually_on_offer() -> None:
    """The guard against over-correcting."""
    view = _view(sessions=True, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get())
    assert response.status_code == 401
    assert "resource_metadata=" in response["WWW-Authenticate"]


async def test_async_get_past_the_gate_reaches_the_session_checks() -> None:
    """An authenticated caller on that same configuration is not short-circuited."""
    view = _view(sessions=True, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get(principal="alice"))
    assert response.status_code == 404


# ---------- DELETE answers the same question without a credential ----------


@pytest.mark.parametrize("is_async", [False, True])
async def test_delete_answers_405_without_a_credential(is_async: bool) -> None:
    """The same condition, ahead of authentication."""
    view = _view(sessions=False, is_async=is_async)
    request = factory.delete("/mcp/")
    response = await view(request) if is_async else view(request)
    assert response.status_code == 405


@pytest.mark.parametrize("is_async", [False, True])
async def test_delete_discloses_the_setting_to_anyone_who_asks(is_async: bool) -> None:
    """Why hiding GET's ``405`` was never worth a spurious OAuth flow."""
    off = _view(sessions=False, is_async=is_async)
    on = _view(sessions=True, is_async=is_async)
    off_request, on_request = factory.delete("/mcp/"), factory.delete("/mcp/")
    off_response = await off(off_request) if is_async else off(off_request)
    on_response = await on(on_request) if is_async else on(on_request)
    assert off_response.status_code == 405
    assert on_response.status_code == 401
