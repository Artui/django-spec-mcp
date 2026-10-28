"""An in-process call consumes nothing of a request it is handed.

``copy_request`` gives each call its own body stream, so a spec reading the
body leaves the caller's request readable, whether Django built it from WSGI or
ASGI, and however many calls share it.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from io import BytesIO, RawIOBase
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest
from django.http.request import RawPostDataException
from django.test import RequestFactory, override_settings
from django_service_specs import SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

_BODY = b'{"a":1}'


def _wsgi() -> HttpRequest:
    return RequestFactory().post("/view", data=_BODY, content_type="application/json")


def _asgi(length: str | None = str(len(_BODY))) -> HttpRequest:
    # As Django's ASGI handler builds one: the body spooled into a file the
    # request closes once it has read it, with ``content-length`` as declared.
    body_file = tempfile.SpooledTemporaryFile()  # noqa: SIM115 - the request closes it
    body_file.write(_BODY)
    body_file.seek(0)
    headers = [(b"content-type", b"application/json")]
    if length is not None:
        headers.append((b"content-length", length.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/view",
        "query_string": b"",
        "headers": headers,
    }
    return ASGIRequest(scope, body_file)


class _Unseekable:
    """A stream that reads and cannot seek, with or without saying so."""

    def __init__(self, *, says: bool) -> None:
        self._body = BytesIO(_BODY)
        if says:
            self.seekable = lambda: False

    def read(self, *size: int) -> bytes:
        return self._body.read(*size)

    def close(self) -> None:
        """Django closes a stream once it has read the body from it."""


class _Gone(RawIOBase):
    """A client that went away mid-body."""

    def readable(self) -> bool:
        return True

    def read(self, *_: Any) -> bytes:
        raise OSError("client went away")


def _with_stream(stream: Any) -> HttpRequest:
    request = HttpRequest()
    request.method = "POST"
    request._stream = stream  # type: ignore[attr-defined]
    request._read_started = False  # type: ignore[attr-defined]
    return request


def _with_length(length: str) -> HttpRequest:
    request = _wsgi()
    request.META["CONTENT_LENGTH"] = length
    return request


_UNREAD: dict[str, Callable[[], HttpRequest]] = {"wsgi": _wsgi, "asgi": _asgi}


def _server() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    for name, selector in {
        "body": lambda *, request: {"read": request.body.decode()},
        "drain": lambda *, request: {"read": request.read().decode()},
        "ignore": lambda *, request: {"read": ""},
    }.items():
        server.register_tool(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=selector, permissions=[Unrestricted()]
            ),
            name=name,
        )
    return server


async def _call(server: MCPServer, how: str, name: str, request: HttpRequest) -> str:
    if how == "call_tool":
        result = await sync_to_async(server.call_tool)(name, user=None, request=request)
        return result.structured_content["read"]
    answer: Any = await server.acall_tool(name, user=None, request=request)
    return answer["structuredContent"]["read"]


@pytest.mark.parametrize("how", ["call_tool", "acall_tool"])
@pytest.mark.parametrize("kind", ["wsgi", "asgi"])
async def test_a_spec_reading_the_body_leaves_the_handed_requests_body_intact(
    kind: str, how: str
) -> None:
    handed = _UNREAD[kind]()

    read = await _call(_server(), how, "body", handed)

    assert read == _BODY.decode()
    assert handed.body == _BODY


async def test_concurrent_calls_on_one_asgi_request_each_read_the_body() -> None:
    server = _server()
    handed = _asgi()

    reads = await asyncio.gather(
        server.acall_tool("body", user=None, request=handed),
        server.acall_tool("body", user=None, request=handed),
    )

    assert [answer["structuredContent"]["read"] for answer in reads] == [_BODY.decode()] * 2
    assert handed.body == _BODY


@pytest.mark.parametrize("how", ["call_tool", "acall_tool"])
async def test_a_spec_draining_the_stream_leaves_the_originals_stream_whole(how: str) -> None:
    """Reading the stream itself, not the cached ``body``, drains only the
    clone's own stream."""
    handed = _wsgi()

    read = await _call(_server(), how, "drain", handed)

    assert read == _BODY.decode()
    assert handed.read() == _BODY


@pytest.mark.parametrize(
    "handed",
    [
        pytest.param(_wsgi, id="wsgi"),
        pytest.param(_asgi, id="asgi-with-length"),
        pytest.param(lambda: _asgi(None), id="asgi-without-length"),
    ],
)
@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=2)
def test_an_oversized_body_is_left_unread_for_the_caller(
    handed: Callable[[], HttpRequest],
) -> None:
    """Over the limit, the body is not read for the copy, so a call whose spec
    never reads it runs, and the caller's stream is where it was."""
    request = handed()

    result = _server().call_tool("ignore", user=None, request=request)

    assert result.structured_content == {"read": ""}
    assert request.read() == _BODY


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=5)
def test_an_understated_length_over_the_limit_is_left_unread() -> None:
    """An ASGI body declaring less than it holds is measured on its stream, as
    Django's ``body`` measures it before refusing it."""
    request = _asgi("3")

    result = _server().call_tool("ignore", user=None, request=request)

    assert result.structured_content == {"read": ""}
    assert request.read() == _BODY


async def test_an_asgi_body_with_no_length_within_the_limit_is_read_once() -> None:
    request = _asgi(None)

    read = await _call(_server(), "acall_tool", "body", request)

    assert read == _BODY.decode()
    assert request.body == _BODY


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=None)
def test_no_upload_limit_reads_every_body_of_known_size() -> None:
    request = _wsgi()

    result = _server().call_tool("body", user=None, request=request)

    assert result.structured_content == {"read": _BODY.decode()}
    assert request.body == _BODY


@pytest.mark.parametrize(
    "handed",
    [
        pytest.param(lambda: _with_length("abc"), id="malformed-length"),
        pytest.param(lambda: _with_length("-1"), id="negative-length"),
        pytest.param(lambda: _with_stream(_Unseekable(says=True)), id="unseekable"),
        pytest.param(lambda: _with_stream(_Unseekable(says=False)), id="no-seekable"),
    ],
)
def test_a_body_of_unknown_size_is_shared(handed: Callable[[], HttpRequest]) -> None:
    """A size not known before reading is not read for the copy: the copy
    reads the caller's own stream, so what the spec reads the caller no longer
    can."""
    request = handed()

    result = _server().call_tool("drain", user=None, request=request)

    assert result.structured_content == {"read": _BODY.decode()}
    assert request.read() == b""


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=2)
def test_an_unseekable_body_with_no_length_over_the_limit_is_not_truncated() -> None:
    request = _with_stream(_Unseekable(says=True))

    _server().call_tool("ignore", user=None, request=request)

    assert request.read() == _BODY


def test_an_unreadable_body_does_not_fail_the_call() -> None:
    """A client gone mid-body fails the pre-read, which a call whose spec never
    reads the body is not failed for; the stream is gone, and the caller's
    ``body`` says it was read."""
    request = _wsgi()
    request._stream = _Gone()  # type: ignore[attr-defined]

    result = _server().call_tool("ignore", user=None, request=request)

    assert result.structured_content == {"read": ""}
    with pytest.raises(RawPostDataException):
        request.body  # noqa: B018
