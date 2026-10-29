"""Wire-conformance tests for transport auth ordering + session binding.

Each rule runs on both views through ``make_view``, except GET, which only
the async view can serve a stream on:

- POST authenticates *before* the session lookup (no 404-vs-401 oracle).
- GET (SSE) and DELETE require authentication wherever a credential can
  change the answer — which on GET means the async view with a broker
  wired and sessions on. Where the answer is 405 whatever the caller
  presents, that is settled first; see
  ``test_unauthenticated_method_support.py``.
- Sessions are bound to the principal that initialized them; another
  principal presenting the id sees the same 404 as an unknown session.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
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


class _HeaderPrincipalBackend:
    """Authenticates via the ``X-Principal`` header — distinct test principals."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        principal: str | None = request.headers.get("X-Principal")
        if principal is None:
            return None
        return TokenInfo(user=SimpleNamespace(pk=principal))

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(self, *, scopes: Any = None, error: Any = None) -> str:
        del scopes, error
        return 'Bearer realm="x", error="invalid_token"'


def _sync_view(store: InMemorySessionStore) -> Any:
    return StreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        auth_backend=_HeaderPrincipalBackend(),
        session_store=store,
        config=build_mcp_config(),
    )


def _async_view(store: InMemorySessionStore, *, broker: InMemorySSEBroker | None = None) -> Any:
    return AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        auth_backend=_HeaderPrincipalBackend(),
        session_store=store,
        sse_broker=broker,
        config=build_mcp_config(),
    )


@pytest.fixture(params=[_sync_view, _async_view], ids=["sync", "async"])
def make_view(request: pytest.FixtureRequest) -> Callable[[InMemorySessionStore], Any]:
    """Build the sync and then the async view, both judging by ``X-Principal``."""
    return request.param


async def _answer(view: Any, request: HttpRequest) -> Any:
    """Call ``view``, awaiting the async view's coroutine."""
    response: Any = view(request)
    return await response if inspect.isawaitable(response) else response


async def _post(
    view: Any,
    *,
    method: str = "ping",
    session_id: str | None = None,
    principal: str | None = None,
    request_id: int = 1,
) -> Any:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if method == "initialize":
        payload["params"] = {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "x", "version": "0"},
        }
    headers: dict[str, str] = {"Mcp-Protocol-Version": "2025-11-25"}
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    if principal is not None:
        headers["X-Principal"] = principal
    request = factory.post(
        "/mcp/", data=json.dumps(payload), content_type="application/json", headers=headers
    )
    return await _answer(view, request)


# ---------- POST: auth before session lookup ----------


async def test_unauthenticated_post_with_bogus_session_is_401_not_404(make_view: Any) -> None:
    """Session validity must not be probeable without a credential."""
    response = await _post(make_view(InMemorySessionStore()), session_id="made-up-id")
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")


async def test_authenticated_post_with_bogus_session_is_404(make_view: Any) -> None:
    view = make_view(InMemorySessionStore())
    response = await _post(view, session_id="made-up-id", principal="alice")
    assert response.status_code == 404


# ---------- POST: session bound to principal ----------


async def test_session_is_bound_to_initializing_principal(make_view: Any) -> None:
    store = InMemorySessionStore()
    view = make_view(store)
    init = await _post(view, method="initialize", principal="alice")
    assert init.status_code == 200
    sid = init["Mcp-Session-Id"]
    assert store.owner(sid) == "user:alice"

    same = await _post(view, session_id=sid, principal="alice", request_id=2)
    assert same.status_code == 200

    other = await _post(view, session_id=sid, principal="mallory", request_id=3)
    assert other.status_code == 404


# ---------- GET: the stream is bound to its principal too ----------
#
# Whether GET authenticates at all, on each view and configuration, is held by
# ``test_unauthenticated_method_support.py``.


async def test_async_get_wrong_principal_is_404() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    view = _async_view(store, broker=InMemorySSEBroker())
    response = await view(
        factory.get(
            "/mcp/",
            headers={
                "X-Principal": "mallory",
                "Mcp-Protocol-Version": "2025-11-25",
                "Mcp-Session-Id": sid,
            },
        )
    )
    assert response.status_code == 404


# ---------- DELETE: authentication + principal binding ----------


async def test_delete_requires_authentication(make_view: Any) -> None:
    response = await _answer(make_view(InMemorySessionStore()), factory.delete("/mcp/"))
    assert response.status_code == 401


async def test_delete_wrong_principal_is_404_and_session_survives(make_view: Any) -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    response = await _answer(
        make_view(store),
        factory.delete("/mcp/", headers={"X-Principal": "mallory", "Mcp-Session-Id": sid}),
    )
    assert response.status_code == 404
    assert store.exists(sid)


async def test_delete_owner_destroys_session(make_view: Any) -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    response = await _answer(
        make_view(store),
        factory.delete("/mcp/", headers={"X-Principal": "alice", "Mcp-Session-Id": sid}),
    )
    assert response.status_code == 204
    assert not store.exists(sid)


# ---------- modern era: the same rules, minus the session ----------


async def _modern_post(view: Any, *, principal: str | None = None, method: str = "ping") -> Any:
    """A modern POST: per-request ``_meta``, mirrored headers, no session."""
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers: dict[str, str] = {
        "Mcp-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if principal is not None:
        headers["X-Principal"] = principal
    request = factory.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"_meta": meta}}),
        content_type="application/json",
        headers=headers,
    )
    return await _answer(view, request)


async def test_an_unauthenticated_modern_post_is_401(make_view: Any) -> None:
    """Statelessness removes the session, not the credential."""
    response = await _modern_post(make_view(InMemorySessionStore()))
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")


async def test_an_authenticated_modern_post_needs_no_session(make_view: Any) -> None:
    response = await _modern_post(make_view(InMemorySessionStore()), principal="alice")
    assert response.status_code == 200
    assert "Mcp-Session-Id" not in response.headers


async def test_a_modern_notification_is_202(make_view: Any) -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    request = factory.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {"_meta": meta}}
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2026-07-28", "X-Principal": "alice"},
    )
    response = await _answer(make_view(InMemorySessionStore()), request)
    assert response.status_code == 202


async def test_an_unknown_modern_method_is_404(make_view: Any) -> None:
    """The 404 is what lets a client tell this endpoint from a legacy HTTP+SSE
    server; the JSON-RPC body is what stops it falling back anyway."""
    response = await _modern_post(
        make_view(InMemorySessionStore()), principal="alice", method="nonsense/method"
    )
    assert response.status_code == 404
    assert json.loads(response.content)["error"]["code"] == -32601


async def test_modern_delete_is_405(make_view: Any) -> None:
    """Session termination does not exist in the revision the caller named."""
    request = factory.delete("/mcp/", headers={"Mcp-Protocol-Version": "2026-07-28"})
    response = await _answer(make_view(InMemorySessionStore()), request)
    assert response.status_code == 405
