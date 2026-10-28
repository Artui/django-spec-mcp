"""An in-process call handed a DRF ``Request`` writes to neither it nor the
``HttpRequest`` it wraps."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory
from django_service_specs import SelectorKind, SelectorSpec, Unrestricted
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

_BODY = b'{"a":1}'


class _User:
    is_authenticated = True

    def __init__(self, name: str) -> None:
        self.username = name


def _server(spec: Callable[[Any], Any] | None = None) -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    if spec is not None:
        server.register_tool(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, request: {"read": spec(request)},
                permissions=[Unrestricted()],
            ),
            name="t",
        )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"user": request.user.username},
            permissions=[Unrestricted()],
        ),
        name="who",
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"read": request.stream.read().decode()},
            permissions=[Unrestricted()],
        ),
        name="drain",
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"data": dict(request.data)},
            permissions=[Unrestricted()],
        ),
        name="data",
    )
    return server


def _wrapped(user: Any, inner: HttpRequest | None = None) -> Request:
    handed = Request(inner if inner is not None else HttpRequest(), parsers=[JSONParser()])
    # Through DRF's setter, which writes the wrapped request's ``user`` too.
    handed.user = user
    return handed


@pytest.mark.parametrize("how", ["call_tool", "acall_tool"])
async def test_a_handed_drf_request_keeps_its_user(how: str) -> None:
    owner = _User("owner")
    caller = _User("caller")
    handed = _wrapped(owner)
    server = _server()

    if how == "call_tool":
        answer: Any = server.call_tool("who", user=caller, request=handed).structured_content
    else:
        answer = (await server.acall_tool("who", user=caller, request=handed))["structuredContent"]

    assert answer == {"user": "caller"}
    assert handed.user is owner
    assert handed._request.user is owner


def test_a_drf_request_whose_stream_is_open_is_drained_for_the_caller() -> None:
    """Once DRF has opened its stream over the wrapped request, a spec reading
    it reads the caller's, as it would had the request itself been handed."""
    inner = RequestFactory().post("/view", data=_BODY, content_type="application/json")
    handed = _wrapped(AnonymousUser(), inner)
    assert handed.stream is inner

    result = _server().call_tool("drain", user=None, request=handed)

    assert result.structured_content == {"read": _BODY.decode()}
    assert handed.stream is inner
    assert handed.stream.read() == b""


@pytest.mark.parametrize(
    "inner",
    [
        lambda: RequestFactory().post("/view", data=_BODY, content_type="application/json"),
        lambda: RequestFactory().post(
            "/view", data={"a": "1", "f": SimpleUploadedFile("a.txt", b"xyz")}
        ),
    ],
    ids=["json", "multipart"],
)
def test_a_drf_request_a_spec_parsed_is_parsed_for_the_caller(inner: Any) -> None:
    """DRF's parse cache is shared with the handed wrapper, so its ``data``,
    ``POST`` and ``FILES`` are the spec's parse, and its stream is the request
    it wraps, not the copy's, where a second parse would find the stream
    consumed."""
    wrapped = inner()
    handed = Request(wrapped, parsers=[JSONParser(), FormParser(), MultiPartParser()])
    parsed: list[Any] = []

    def spec(request: Any) -> str:
        parsed.append((request.data, request.POST, request.FILES))
        return "parsed"

    _server(spec).call_tool("t", user=None, request=handed)

    data, post, files = parsed[0]
    assert handed.data is data
    # A form's ``POST`` is the parse itself; any other body's is a fresh empty one.
    assert post == handed.POST
    assert handed.FILES is files
    assert handed.stream is wrapped
    assert handed.data["a"] in (1, "1")


def test_a_drf_request_with_an_empty_body_still_parses_as_empty() -> None:
    """DRF opens no stream over an empty body, ``_stream`` being ``None``; the
    copy keeps that rather than pointing it at a request with nothing to
    parse."""
    inner = RequestFactory().post("/view", data=b"", content_type="application/json")
    handed = _wrapped(AnonymousUser(), inner)
    assert handed.stream is None

    result = _server().call_tool("data", user=None, request=handed)

    assert result.structured_content == {"data": {}}
