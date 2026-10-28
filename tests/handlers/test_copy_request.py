"""An in-process call shares the handed request's body and parse state; only its user is its own.

``copy_request`` hands each call a request that reads and writes the body, the
stream and whatever has been parsed from them on the handed request itself, so
what a spec reads is read for the caller exactly as if the request had been
handed over, whether Django built it from WSGI or ASGI and however many calls
share it, while the user the call seats stays on the copy.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest
from django.http.multipartparser import MultiPartParser
from django.http.request import RawPostDataException
from django.test import RequestFactory
from django_service_specs import SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

_BODY = b'{"a":1}'


class _User:
    is_authenticated = True

    def __init__(self, name: str) -> None:
        self.username = name


def _wsgi() -> HttpRequest:
    return RequestFactory().post("/view", data=_BODY, content_type="application/json")


def _asgi() -> HttpRequest:
    # As Django's ASGI handler builds one: the body spooled into a file the
    # request closes once it has read it, with ``content-length`` as declared.
    body_file = tempfile.SpooledTemporaryFile()  # noqa: SIM115 - the request closes it
    body_file.write(_BODY)
    body_file.seek(0)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/view",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(_BODY)).encode()),
        ],
    }
    return ASGIRequest(scope, body_file)


def _form() -> HttpRequest:
    return RequestFactory().post(
        "/view", data={"name": "n", "f": SimpleUploadedFile("a.txt", b"xyz")}
    )


_UNREAD: dict[str, Callable[[], HttpRequest]] = {"wsgi": _wsgi, "asgi": _asgi}


def _server(spec: Callable[[Any], Any]) -> MCPServer:
    """A server whose one tool, ``t``, answers what ``spec`` returns for its request."""
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, request: {"read": spec(request)},
            permissions=[Unrestricted()],
        ),
        name="t",
    )
    return server


async def _call(how: str, spec: Callable[[Any], Any], request: Any, *, user: Any = None) -> Any:
    server = _server(spec)
    if how == "call_tool":
        result = await sync_to_async(server.call_tool)("t", user=user, request=request)
        return result.structured_content["read"]
    answer: Any = await server.acall_tool("t", user=user, request=request)
    assert not answer.get("isError"), answer
    return answer["structuredContent"]["read"]


_HOW = pytest.mark.parametrize("how", ["call_tool", "acall_tool"])


@_HOW
@pytest.mark.parametrize("kind", ["wsgi", "asgi"])
async def test_a_spec_reading_the_body_reads_it_for_the_caller(kind: str, how: str) -> None:
    """The body a spec reads is cached on the handed request, and the stream
    that read leaves behind is the caller's, as one read of its own would."""
    handed = _UNREAD[kind]()

    read = await _call(how, lambda request: request.body.decode(), handed)

    assert read == _BODY.decode()
    assert handed.body == _BODY
    assert handed.read() == _BODY


@_HOW
async def test_a_spec_draining_the_stream_drains_it_for_the_caller(how: str) -> None:
    """Reading the stream itself, not the cached ``body``, consumes it for the
    caller too, who then gets what Django answers any request read that way."""
    handed = _wsgi()

    read = await _call(how, lambda request: request.read().decode(), handed)

    assert read == _BODY.decode()
    # The body first, since the caller's own read would mark the stream read.
    with pytest.raises(RawPostDataException):
        handed.body  # noqa: B018 - the read is the assertion
    assert handed.read() == b""


@_HOW
@pytest.mark.parametrize("kind", ["wsgi", "asgi"])
async def test_a_body_no_spec_reads_is_left_unread(kind: str, how: str) -> None:
    """A call reading nothing reads nothing on the caller's behalf, so the
    caller's own first read starts the stream as it would have."""
    handed = _UNREAD[kind]()

    await _call(how, lambda request: "", handed)

    assert handed.read(3) == _BODY[:3]
    with pytest.raises(RawPostDataException):
        handed.body  # noqa: B018 - the read is the assertion


@_HOW
async def test_a_closed_stream_does_not_fail_a_call_that_reads_nothing(how: str) -> None:
    """An ASGI request outliving its response has a closed body file; a call
    that never reads the body is not failed for it."""
    handed = _asgi()
    handed._stream.close()  # type: ignore[attr-defined]

    assert await _call(how, lambda request: "ok", handed) == "ok"


@_HOW
async def test_a_form_a_spec_parsed_is_parsed_for_the_caller(how: str) -> None:
    """``POST`` and ``FILES`` parsed off the stream by a spec are the caller's
    own, where a second parse would find the stream consumed and answer
    empty."""
    handed = _form()

    read = await _call(how, lambda request: [request.POST["name"], request.FILES["f"].name], handed)

    assert read == ["n", "a.txt"]
    assert handed.POST["name"] == "n"
    assert handed.FILES["f"].read() == b"xyz"


@_HOW
async def test_an_encoding_a_spec_sets_is_the_callers(how: str) -> None:
    """Setting ``encoding`` discards the parsed ``POST`` so it is parsed again
    under the new one: the caller's, which is then the ``POST`` the spec
    parsed, under the encoding the spec set."""
    handed = RequestFactory().post(
        "/view", data=b"name=n", content_type="application/x-www-form-urlencoded"
    )
    before = handed.POST
    parsed: list[Any] = []

    def spec(request: Any) -> str:
        # Spelled so that every Django accepts it for a urlencoded form.
        request.encoding = "UTF-8"
        parsed.append(request.POST)
        return request.POST["name"]

    assert await _call(how, spec, handed) == "n"
    assert handed.encoding == "UTF-8"
    assert handed.POST is parsed[0]
    assert handed.POST is not before


@_HOW
async def test_upload_handlers_a_spec_sets_are_the_callers(how: str) -> None:
    handlers = [MemoryFileUploadHandler()]

    def spec(request: Any) -> str:
        request.upload_handlers = handlers
        return request.FILES["f"].name

    handed = _form()

    assert await _call(how, spec, handed) == "a.txt"
    # Django freezes them as an ``ImmutableList`` once it parses with them.
    assert list(handed.upload_handlers) == handlers


@pytest.mark.skipif(
    not hasattr(HttpRequest, "multipart_parser_class"),
    reason="Django 6.1 added multipart_parser_class",
)
@_HOW
async def test_a_multipart_parser_a_spec_sets_is_the_callers(how: str) -> None:
    class _Parser(MultiPartParser):
        pass

    def spec(request: Any) -> str:
        request.multipart_parser_class = _Parser
        return request.FILES["f"].name

    handed = _form()

    assert await _call(how, spec, handed) == "a.txt"
    assert handed.multipart_parser_class is _Parser


async def test_concurrent_calls_on_one_asgi_request_keep_their_own_users() -> None:
    """Two calls on one handed request each see their own user and the one
    body, which the caller can still read afterwards."""
    owner = _User("owner")
    handed = _asgi()
    handed.user = owner
    server = _server(lambda request: [request.user.username, request.body.decode()])

    first, second = await asyncio.gather(
        server.acall_tool("t", user=_User("first"), request=handed),
        server.acall_tool("t", user=_User("second"), request=handed),
    )

    assert first["structuredContent"]["read"] == ["first", _BODY.decode()]
    assert second["structuredContent"]["read"] == ["second", _BODY.decode()]
    assert handed.user is owner
    assert not hasattr(handed, "mcp_token")
    assert handed.body == _BODY


@_HOW
async def test_a_spec_handing_its_request_on_shares_the_callers_body(how: str) -> None:
    """A spec handing its own request to a further call hands a copy of a
    copy, which shares the body with the request first handed and carries the
    inner call's user only."""
    inner = _server(lambda request: [request.user.username, request.body.decode()])
    handed = _wsgi()
    handed.user = _User("owner")

    def spec(request: Any) -> Any:
        nested = inner.call_tool("t", user=_User("inner"), request=request)
        return [*nested.structured_content["read"], request.user.username]

    read = await _call(how, spec, handed, user=_User("outer"))

    assert read == ["inner", _BODY.decode(), "outer"]
    assert handed.user.username == "owner"
    assert handed.body == _BODY


async def test_a_copy_reads_as_the_request_it_copies() -> None:
    """The copy is an instance of the handed request's class, under its name."""
    seen: list[Any] = []
    handed = _wsgi()

    def spec(request: Any) -> str:
        seen.append(request)
        return repr(request)

    assert await _call("call_tool", spec, handed) == repr(handed)
    assert isinstance(seen[0], type(handed))
    assert type(seen[0]).__name__ == type(handed).__name__


async def test_deleting_an_attribute_on_the_copy_leaves_the_caller_its_own() -> None:
    """Only the body and parse state are shared: an attribute of the caller's
    own that a spec deletes is deleted from the copy alone."""
    handed = _wsgi()
    handed.note = "kept"  # type: ignore[attr-defined]

    def spec(request: Any) -> bool:
        del request.note
        return hasattr(request, "note")

    assert await _call("call_tool", spec, handed) is False
    assert handed.note == "kept"  # type: ignore[attr-defined]


_EMPTY = object()


class _Wrapped:
    """The shape of DRF's ``Request`` that ``copy_request`` reads, built without DRF.

    The ``HttpRequest`` it wraps is ``_request``, its ``user`` setter writes
    through to it, and ``parse`` opens a stream over the wrapped request and
    caches what it parsed as DRF's ``data`` does, under DRF's names.
    """

    def __init__(self, request: HttpRequest) -> None:
        self._request = request
        self._data: Any = _EMPTY
        self._files: Any = _EMPTY
        self._full_data: Any = _EMPTY
        self._stream: Any = _EMPTY

    @property
    def user(self) -> Any:
        return self._user

    @user.setter
    def user(self, value: Any) -> None:
        self._user = value
        self._request.user = value

    def parse(self) -> Any:
        if self._full_data is _EMPTY:
            self._stream = self._request
            self._data = json.loads(self._stream.read())
            self._files = {}
            self._full_data = self._data
        return self._full_data


@_HOW
async def test_a_wrapped_request_keeps_its_user(how: str) -> None:
    """The wrapper's ``user`` setter writes through to the request it wraps,
    so that one is copied too, and neither the handed wrapper nor what it
    wraps names the call's user afterwards."""
    owner = _User("owner")
    handed = _Wrapped(_wsgi())
    handed.user = owner

    read = await _call(how, lambda request: request.user.username, handed, user=_User("caller"))

    assert read == "caller"
    assert handed.user is owner
    assert handed._request.user is owner


@_HOW
async def test_a_wrapped_requests_parse_is_the_callers(how: str) -> None:
    """What a spec parses through the wrapper is cached on the caller's
    wrapper, whose stream is its own wrapped request rather than the copy's,
    so the caller reads the parse instead of parsing a consumed stream."""
    inner = _wsgi()
    handed = _Wrapped(inner)

    read = await _call(how, lambda request: request.parse(), handed)

    assert read == {"a": 1}
    assert handed._data == {"a": 1}
    assert handed._files == {}
    assert handed._full_data == {"a": 1}
    assert handed._stream is inner
    assert handed.parse() == {"a": 1}
