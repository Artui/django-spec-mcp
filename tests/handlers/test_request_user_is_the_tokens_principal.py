"""``request.user`` is the token's principal on every path that hands a ``request``.

A kernel tool's spec, a prompt's ``render``, a completer and a pool seed resolved
on a view's read are each handed the transport's ``HttpRequest``. Over the wire
its ``user`` is whatever the project's middleware resolved, a session cookie
included, and the MCP caller is the bearer token's principal, which can be
someone else. drf-mcp handed these a DRF ``Request`` whose ``user`` was the
token's, so a callable authorizing on ``request.user`` was judging the caller;
the request is held to that here. ``_SessionStandIn`` resolves a session's user
exactly where ``AuthenticationMiddleware`` would, as ``request.user`` and
``request.auser``, from a header naming the cookie's user.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings
from django.utils.functional import SimpleLazyObject
from django_service_specs import PoolSeeds, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

_SESSION_USER_HEADER = "X-Session-User"


def _session_stand_in(get_response: Callable[[HttpRequest], HttpResponse]) -> Any:
    """``AuthenticationMiddleware``, with the session's user named by a header."""

    def middleware(request: HttpRequest) -> HttpResponse:
        username = request.headers.get(_SESSION_USER_HEADER)

        def session_user() -> Any:
            found = get_user_model().objects.filter(username=username).first()
            return found if found is not None else AnonymousUser()

        async def auser() -> Any:
            return await sync_to_async(session_user)()

        request.user = SimpleLazyObject(session_user)
        request.auser = auser
        return get_response(request)

    return middleware


_MIDDLEWARE = [f"{__name__}._session_stand_in"]


def _whoami(request: Any) -> str:
    return _name(request.user)


def _name(user: Any) -> str:
    return "anonymous" if not user.is_authenticated else user.username


def _async_name(request: Any) -> str:
    return _name(async_to_sync(request.auser)())


def _server(seen: list[Any]) -> MCPServer:
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
    server = _server(seen)
    with override_settings(
        ROOT_URLCONF=urlconf_for(server, is_async=is_async), MIDDLEWARE=_MIDDLEWARE
    ):
        caller = KernelClient(client, user=token_user)
        client.defaults["HTTP_X_SESSION_USER"] = "cookie-owner"
        caller.initialize()
        tool = caller.call("whoami")["result"]["structuredContent"]["user"]
        prompt = caller.post("prompts/get", {"name": "whoami"}).json()["result"]
        completion = caller.post(
            "completion/complete",
            {"ref": {"type": "ref/prompt", "name": "whoami"}, "argument": {"name": "topic"}},
        ).json()["result"]
        seen.clear()
        caller.post("resources/read", {"uri": "ui://board.html"})
    return {
        "tool": tool,
        "prompt": prompt["messages"][0]["content"]["text"],
        "completion": completion["completion"]["values"][0],
        "view_seed": f"{_whoami(seen[0])}/{_async_name(seen[0])}",
    }


@pytest.mark.django_db(transaction=True)
def test_every_path_sees_the_tokens_principal_not_the_sessions(
    client: Client, users: None, is_async: bool
) -> None:
    answers = _answers(client, token_user="token-caller", is_async=is_async)

    assert answers == dict.fromkeys(
        ("tool", "prompt", "completion", "view_seed"), "token-caller/token-caller"
    )


@pytest.mark.django_db(transaction=True)
def test_a_caller_the_token_names_no_one_is_anonymous_not_the_sessions_user(
    client: Client, users: None, is_async: bool
) -> None:
    answers = _answers(client, token_user=None, is_async=is_async)

    assert answers == dict.fromkeys(
        ("tool", "prompt", "completion", "view_seed"), "anonymous/anonymous"
    )
