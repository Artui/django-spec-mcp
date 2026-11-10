from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django.test import AsyncClient, Client
from django_service_specs import Parameter, Parameters, ValidationContext, Validator

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.conf import get_setting
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID


def make_server(
    *, name: str = "t", auth_backend: MCPAuthBackend | None = None, **overrides: Any
) -> MCPServer:
    """An ``MCPServer`` named ``t`` that lets any caller in; the keywords replace the rest."""
    return MCPServer(
        name=name,
        auth_backend=AllowAnyBackend() if auth_backend is None else auth_backend,
        **overrides,
    )


def tool_error(out: Any) -> dict[str, Any]:
    """Assert ``out`` is an ``isError`` tool result; return its error object."""
    assert isinstance(out, dict), f"expected a tool-result dict, got {out!r}"
    assert out.get("isError") is True
    assert "structuredContent" not in out
    return json.loads(out["content"][0]["text"])["error"]


def plain_context(server: MCPServer, **overrides: Any) -> MCPCallContext:
    """An anonymous legacy-era context carrying only ``server``'s three registries."""
    base: dict[str, Any] = {
        "http_request": HttpRequest(),
        "token": TokenInfo(user=None),
        "tools": server.tools,
        "resources": server.resources,
        "prompts": server.prompts,
        "protocol_version": "2025-11-25",
    }
    base.update(overrides)
    return MCPCallContext(**base)


def call_context(server: MCPServer, **overrides: Any) -> MCPCallContext:
    """A context for calling ``server``'s handlers in process, as an anonymous caller."""
    request = HttpRequest()
    request.method = "POST"
    request.user = None
    base: dict[str, Any] = {
        "http_request": request,
        "token": TokenInfo(user=None),
        "tools": server._tools,
        "resources": server._resources,
        "prompts": server._prompts,
        "protocol_version": "2026-07-28",
        "client_capabilities": {"extensions": {TASKS_EXTENSION_ID: {}}},
        "tasks": server.task_store,
        "subscriptions": server.subscription_broker,
        "config": server._config,
        "exception_map": server._exception_map,
    }
    base.update(overrides)
    return MCPCallContext(**base)


class TypedByTheCall(Validator):
    """Declares ``project`` an integer to a call of ``action``, and any value to no call."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.handed: list[dict[str, Any] | None] = []

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        self.handed.append(call_context)
        to_the_call = call_context is not None and call_context["action"] == self.action
        return Parameters.of(Parameter("project", "integer" if to_the_call else "any"))

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)

    def distinct(self) -> list[dict[str, Any]]:
        """The dicts handed, each object once, in the order first handed; ``None`` raises."""
        assert None not in self.handed, "a reader was handed no call_context during the call"
        seen: list[dict[str, Any]] = []
        for each in self.handed:
            assert each is not None
            if not any(each is kept for kept in seen):
                seen.append(each)
        return seen


class RefusingRateLimit:
    """An ``MCPRateLimit`` refusing every call, counting the calls it was asked about."""

    def __init__(self) -> None:
        self.consumed: int = 0

    def consume(self, request: Any, token: Any) -> int | None:
        self.consumed += 1
        return 30


# How deep a body must nest before ``json.loads`` raises ``RecursionError``
# depends on where the parse runs. Before Python 3.14 the decoder counts levels
# against a fixed limit. From 3.14 it stops when the C stack runs low, so the
# depth follows the stack: about 74,000 levels on an 8 MiB main thread, and
# about 600,000 under ``make``, which raises the soft stack limit to the hard
# one (64 MiB on macOS) for the processes it starts. A body that deep is over
# the 1 MiB default request cap, so the tests that send one decode it on a
# thread that asks for a 4 MiB stack. There the limit is some tens of thousands
# of levels on 3.14, and the count, about 1,000 or 10,000, on earlier Pythons.
#
# Asks, because on Linux the requested size is a floor rather than the size.
# glibc gives a new thread a cached stack left by one that has exited when that
# stack is at least the size requested and at most four times it, so a thread
# asking for 4 MiB can run on 16 MiB after the default-sized threads of a run
# under ``ulimit -s 16384``, and there 100,000 levels decode on 3.14. No fixed
# depth is past the limit on every run, so the depth is found on the thread that
# decodes the body rather than assumed.
_BOUNDED_STACK_BYTES: int = 4 * 1024 * 1024

# Where the doubling starts: about the limit on 3.10 and 3.11, which count to
# about 1,000 levels, and under every later Python's. So the depth found is
# under twice the limit, and the body no larger than doubling has to make it.
_FIRST_NESTING_DEPTH: int = 1_000


def on_a_bounded_stack(call: Callable[[], Any]) -> Any:
    """Run ``call`` on a new thread that asks for a 4 MiB stack; return its result."""
    previous: int = threading.stack_size(_BOUNDED_STACK_BYTES)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(call).result()
    finally:
        threading.stack_size(previous)


def nested_past_the_decoders_limit_here() -> bytes:
    """Return nested arrays ``json.loads`` raises ``RecursionError`` on, on this thread."""
    cap: int = get_setting("MAX_REQUEST_BYTES")
    depth: int = _FIRST_NESTING_DEPTH
    while 2 * depth <= cap:
        body: bytes = b"[" * depth + b"]" * depth
        try:
            json.loads(body)
        except RecursionError:
            return body
        depth *= 2
    pytest.fail(
        f"nested arrays up to the {cap}-byte request cap all decoded on this thread: "
        "its stack is too large for the decoder's recursion limit to be reached"
    )


# The two protocol eras the endpoint serves: the modern one is sessionless and names its
# method in a header, the legacy one opens a session first.
MODERN: str = "2026-07-28"
LEGACY: str = "2025-11-25"


async def _awaited(response: Any) -> Any:
    return await response


def _send(
    client: Any, is_async: bool, method: str, params: dict[str, Any], headers: dict[str, str]
) -> Any:
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        content_type="application/json",
        headers=headers,
    )
    return async_to_sync(_awaited)(response) if is_async else response


def post_in_era(era: str, is_async: bool, method: str, params: dict[str, Any]) -> Any:
    """One request to the mounted endpoint, in ``era``, on the chosen view; its ``result``."""
    client: Any = AsyncClient() if is_async else Client()
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    body: dict[str, Any] = dict(params)
    if era == MODERN:
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        elif method == "resources/read":
            headers["Mcp-Name"] = params["uri"]
        body["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    else:
        opened = _send(
            client,
            is_async,
            "initialize",
            {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
            {},
        )
        assert opened.status_code == 200, opened.content
        headers["Mcp-Session-Id"] = opened["Mcp-Session-Id"]
    response = _send(client, is_async, method, body, headers)
    assert response.status_code == 200, response.content
    return json.loads(response.content)["result"]
