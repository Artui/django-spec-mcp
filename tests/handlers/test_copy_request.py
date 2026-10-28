"""An in-process call consumes nothing of a request it is handed.

``copy_request`` gives each call its own body stream, so a spec reading the
body leaves the caller's request readable, whether Django built it from WSGI or
ASGI, and however many calls share it.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django_service_specs import SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

_BODY = b'{"a":1}'


def _wsgi() -> HttpRequest:
    return RequestFactory().post("/view", data=_BODY, content_type="application/json")


def _asgi() -> HttpRequest:
    # As Django's ASGI handler builds one: the body spooled into a file the
    # request closes once it has read it.
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


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=2)
def test_an_oversized_unread_body_does_not_refuse_the_call() -> None:
    """Reading a body over the limit raises ``RequestDataTooBig``; a call whose
    spec never reads it runs as it did before the copy read bodies."""
    result = _server().call_tool("ignore", user=None, request=_wsgi())

    assert result.structured_content == {"read": ""}
