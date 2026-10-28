"""``request.user`` is the token's principal on every path that hands a ``request``.

A kernel tool's spec, a prompt's ``render``, a completer and a pool seed resolved
on a view's read are each handed the transport's ``HttpRequest``. Over the wire
its ``user`` is whatever the project's middleware resolved, a session cookie
included, and the MCP caller is the bearer token's principal, which can be
someone else. drf-mcp handed these a DRF ``Request`` whose ``user`` was the
token's, so a callable authorizing on ``request.user`` was judging the caller;
the request is held to that here, behind Django's own session middleware with
a client logged in as someone the token does not name.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import PoolSeeds, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

# Django's own session and authentication middleware, so the user a cookie names
# is resolved by the producer that resolves it in a project. Signed-cookie
# sessions need no table, and the suite installs no sessions app.
_SESSION_SETTINGS: dict[str, Any] = {
    "MIDDLEWARE": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ],
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
}


def _whoami(request: Any) -> str:
    return _name(request.user)


def _name(user: Any) -> str:
    return "anonymous" if not user.is_authenticated else user.username


def _async_name(request: Any) -> str:
    return _name(async_to_sync(request.auser)())


def _server(seen: list[Any], view_read: list[Any] | None = None) -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=HeaderAuthBackend(),
        session_store=InMemorySessionStore(),
        # The async read resolves seeds on the loop, so the request is kept
        # and read once the call is over.
        pool_seeds=PoolSeeds().extend(who=lambda request: seen.append(request)),
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"user": f"{_whoami(request)}/{_async_name(request)}"},
            permissions=[Unrestricted()],
        ),
        name="whoami",
    )
    server.register_prompt(
        name="whoami",
        render=lambda request: f"{_whoami(request)}/{_async_name(request)}",
        arguments=[PromptArgument(name="topic")],
        completions={"topic": lambda request: [f"{_whoami(request)}/{_async_name(request)}"]},
    )
    server.register_ui_resource(name="board", uri="ui://board.html", html="<p>board</p>")
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"user": f"{_whoami(request)}/{_async_name(request)}"},
            permissions=[Unrestricted()],
        ),
        name="kernel_read",
        uri_template="k://who",
    )
    # A view whose selector takes ``request`` reads it from the pool, where the
    # transport lays the request carrying the token.
    kept = view_read if view_read is not None else []
    server.resources.register(
        ResourceBinding(
            name="view_read",
            uri_template="v://who",
            description=None,
            selector=lambda request: kept.append(request) or "ok",
            permissions=(),
            encoding=ResourceEncoding.TEXT,
        )
    )
    return server


@pytest.fixture
def users(db: Any) -> Iterator[None]:
    model = get_user_model()
    model.objects.create_user(username="token-caller")
    model.objects.create_user(username="cookie-owner")
    yield


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    return request.param


def _answers(client: Client, *, token_user: str | None, is_async: bool) -> dict[str, Any]:
    """What each path saw as ``request.user``, as ``<user>/<auser>``."""
    seen: list[Any] = []
    view_read: list[Any] = []
    server = _server(seen, view_read)
    with override_settings(
        ROOT_URLCONF=urlconf_for(server, is_async=is_async), **_SESSION_SETTINGS
    ):
        client.force_login(get_user_model().objects.get(username="cookie-owner"))
        caller = KernelClient(client, user=token_user)
        caller.initialize()
        tool = caller.call("whoami")["result"]["structuredContent"]["user"]
        prompt = caller.post("prompts/get", {"name": "whoami"}).json()["result"]
        completion = caller.post(
            "completion/complete",
            {"ref": {"type": "ref/prompt", "name": "whoami"}, "argument": {"name": "topic"}},
        ).json()["result"]
        seen.clear()
        caller.post("resources/read", {"uri": "ui://board.html"})
        caller.post("resources/read", {"uri": "v://who"})
        kernel_read = caller.post("resources/read", {"uri": "k://who"}).json()["result"]
    return {
        "tool": tool,
        "prompt": prompt["messages"][0]["content"]["text"],
        "completion": completion["completion"]["values"][0],
        "view_seed": f"{_whoami(seen[0])}/{_async_name(seen[0])}",
        "view_read": f"{_whoami(view_read[0])}/{_async_name(view_read[0])}",
        "kernel_read": json.loads(kernel_read["contents"][0]["text"])["user"],
    }


@pytest.mark.django_db(transaction=True)
def test_every_path_sees_the_tokens_principal_not_the_sessions(
    client: Client, users: None, is_async: bool
) -> None:
    answers = _answers(client, token_user="token-caller", is_async=is_async)

    assert answers == dict.fromkeys(
        ("tool", "prompt", "completion", "view_seed", "view_read", "kernel_read"),
        "token-caller/token-caller",
    )


@pytest.mark.django_db(transaction=True)
def test_a_caller_the_token_names_no_one_is_anonymous_not_the_sessions_user(
    client: Client, users: None, is_async: bool
) -> None:
    answers = _answers(client, token_user=None, is_async=is_async)

    assert answers == dict.fromkeys(
        ("tool", "prompt", "completion", "view_seed", "view_read", "kernel_read"),
        "anonymous/anonymous",
    )


@pytest.mark.django_db(transaction=True)
async def test_in_process_no_user_is_anonymous_not_none(users: None) -> None:
    """``acall_tool`` with ``user=None`` builds a request whose ``user`` was
    ``None``; the spec sees an anonymous user, as on the wire."""
    caller = await get_user_model().objects.aget(username="token-caller")
    server = _server([])
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            # ``request.user`` alone, so ``None`` is answered rather than raised on.
            selector=lambda *, request: {
                "user": "none" if request.user is None else _whoami(request)
            },
            permissions=[Unrestricted()],
        ),
        name="who",
    )

    named = await server.acall_tool("who", user=caller)
    nobody = await server.acall_tool("who", user=None)

    assert named["structuredContent"] == {"user": "token-caller"}
    assert nobody["structuredContent"] == {"user": "anonymous"}


def _who_tool(server: MCPServer) -> None:
    """A kernel tool answering the request's user before and after a pause, and
    the user it was called as."""

    async def who(*, request: Any, user: Any) -> dict[str, str]:
        before = _name(request.user)
        await asyncio.sleep(0.01)
        return {"as": _name(user), "before": before, "after": _name(request.user)}

    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=who, permissions=[Unrestricted()]),
        name="who",
    )


def _handed(user: Any) -> HttpRequest:
    request = HttpRequest()
    request.method = "POST"
    request.user = user
    return request


@pytest.mark.django_db(transaction=True)
async def test_an_in_process_call_leaves_the_handed_request_as_it_was(users: None) -> None:
    """A view handing its own request to ``acall_tool`` still serves as its
    user after the call, whoever the call ran as."""
    owner = await get_user_model().objects.aget(username="cookie-owner")
    server = _server([])
    _who_tool(server)
    view_request = _handed(owner)

    await server.acall_tool("who", user=None, request=view_request)

    assert view_request.user is owner
    assert not hasattr(view_request, "mcp_token")


@pytest.mark.django_db(transaction=True)
async def test_concurrent_in_process_calls_on_one_request_keep_their_own_users(
    users: None,
) -> None:
    caller = await get_user_model().objects.aget(username="token-caller")
    owner = await get_user_model().objects.aget(username="cookie-owner")
    server = _server([])
    _who_tool(server)
    shared = _handed(AnonymousUser())

    first, second = await asyncio.gather(
        server.acall_tool("who", user=caller, request=shared),
        server.acall_tool("who", user=owner, request=shared),
    )

    assert first["structuredContent"] == dict.fromkeys(("as", "before", "after"), "token-caller")
    assert second["structuredContent"] == dict.fromkeys(("as", "before", "after"), "cookie-owner")


@pytest.mark.django_db
def test_sync_call_tool_hands_a_copy_carrying_the_acting_user(users: None) -> None:
    """Sync ``call_tool`` agrees with ``acall_tool``: the spec's ``request.user``
    is the ``user=`` it was called as, and the handed request keeps its own."""
    model = get_user_model()
    caller = model.objects.get(username="token-caller")
    owner = model.objects.get(username="cookie-owner")
    server = _server([])
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request, user: {
                "request": _whoami(request),
                # Answered rather than raised on, for a request with no reader.
                "auser": _async_name(request) if hasattr(request, "auser") else "none",
                "user": _name(user),
            },
            permissions=[Unrestricted()],
        ),
        name="sync_who",
    )
    handed = _handed(owner)

    result = server.call_tool("sync_who", user=caller, request=handed)

    assert result.structured_content == {
        "request": "token-caller",
        "auser": "token-caller",
        "user": "token-caller",
    }
    assert handed.user is owner


@pytest.mark.django_db
def test_sync_call_tool_drops_the_handed_requests_token(users: None) -> None:
    """A token on the handed request is the outer caller's, so the spec's copy
    carries none, and the handed request keeps its own."""
    model = get_user_model()
    caller = model.objects.get(username="token-caller")
    owner = model.objects.get(username="cookie-owner")
    server = _server([])
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"token": hasattr(request, "mcp_token")},
            permissions=[Unrestricted()],
        ),
        name="sync_token",
    )
    handed = _handed(owner)
    token = object()
    handed.mcp_token = token

    result = server.call_tool("sync_token", user=caller, request=handed)

    assert result.structured_content == {"token": False}
    assert handed.mcp_token is token


@pytest.mark.django_db
def test_sync_call_tool_with_no_request_hands_one_carrying_the_acting_user(users: None) -> None:
    """With no request handed, sync ``call_tool`` synthesises one bearing the
    user, as ``acall_tool`` and the drf-services path do, rather than handing
    a spec that reads ``request`` a ``None``."""
    caller = get_user_model().objects.get(username="token-caller")
    server = _server([])
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {
                "request": _whoami(request),
                "auser": _async_name(request),
                "method": request.method,
            },
            permissions=[Unrestricted()],
        ),
        name="sync_who",
    )

    result = server.call_tool("sync_who", user=caller)

    assert result.structured_content == {
        "request": "token-caller",
        "auser": "token-caller",
        "method": "POST",
    }
