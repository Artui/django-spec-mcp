"""Shared assertions and helpers for the test suite."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django.test import AsyncClient, Client

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.conf import get_setting
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID


def tool_error(out: Any) -> dict[str, Any]:
    """Assert ``out`` is an ``isError`` tool result; return its error object.

    Tool-level failures (business rules, service-raised validation,
    missing rows) come back as successful JSON-RPC responses whose result
    carries ``isError: true`` and a JSON error payload in ``content[0]``.
    ``structuredContent`` must be absent — it is tied to the success
    ``outputSchema``.
    """
    assert isinstance(out, dict), f"expected a tool-result dict, got {out!r}"
    assert out.get("isError") is True
    assert "structuredContent" not in out
    return json.loads(out["content"][0]["text"])["error"]


def call_context(server: MCPServer, **overrides: Any) -> MCPCallContext:
    """A context for calling ``server``'s handlers in process, as an anonymous caller.

    It declares the tasks extension and carries the server's own task store,
    subscription broker and config, so a handler sees what the server's view
    would hand it; ``overrides`` replace any field. ``tests.tasks.conftest``'s
    ``context`` differs on purpose: it carries the task executor and no broker.
    """
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
    }
    base.update(overrides)
    return MCPCallContext(**base)


class RefusingRateLimit:
    """An ``MCPRateLimit`` refusing every call, counting the calls it was asked about.

    Pins the refusal order on a path that judges permissions before it charges a
    quota: a caller the permission denies is told so, and is never charged.
    """

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
    """Run ``call`` on a new thread that asks for a 4 MiB stack; return its result.

    The bound keeps the nested body small. It is a request rather than a size,
    for the reason the comment above gives, so pair it with
    ``nested_past_the_decoders_limit_here`` called on that thread. The previous
    size is restored however ``call`` ends, so no later thread inherits it.
    """
    previous: int = threading.stack_size(_BOUNDED_STACK_BYTES)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(call).result()
    finally:
        threading.stack_size(previous)


def nested_past_the_decoders_limit_here() -> bytes:
    """Return nested arrays ``json.loads`` raises ``RecursionError`` on, on this thread.

    The depth doubles until the decoder raises here, because the limit follows
    the stack of the calling thread and that stack is not known in advance. Call
    it on the thread that will decode the body: the view decodes deeper in the
    call stack than this probe, with no more stack and no more levels left, so a
    body that overflows here also overflows in the view.

    The loop stops at ``MAX_REQUEST_BYTES``. A body over it would be answered by
    the size cap before the parse, and a stack too large to reach the limit under
    it would otherwise send one or never end, so that case fails the test instead.
    """
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
    """One request to the mounted endpoint, in ``era``, on the chosen view; its ``result``.

    A legacy request opens its own session first; a modern one carries the
    per-request ``_meta`` and the routing headers the era reads.
    """
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
