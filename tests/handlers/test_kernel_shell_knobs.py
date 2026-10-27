"""The ``tools/call`` shell's knobs, taking effect on a kernel binding.

The shell around the dispatch (rate limits, the deadline, the result ceiling and
the invalidation) is shared with the drf-services path and covered by its tests.
These hold that a kernel binding reaches each step, which the coverage gate
cannot show: the lines run either way.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.subscriptions.utils import topic_for_resource
from tests.subscriptions.test_subscription_core import _context
from tests.utils import tool_error

OPEN = [Unrestricted()]


class _Exhausted:
    def consume(self, request: Any, token: Any) -> int | None:
        return 30


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="knobs", auth_backend=AllowAnyBackend(), **kwargs)
    server.register_tool(
        ServiceSpec(service=lambda: {"pk": 1}, permissions=OPEN),
        name="invoices.touch",
        invalidates=("invoices://{pk}",),
    )
    server.register_tool(
        ServiceSpec(service=lambda: {"blob": "x" * 500}, permissions=OPEN),
        name="invoices.big",
        max_result_bytes=200,
    )
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=OPEN),
        name="invoices.limited",
        rate_limits=[_Exhausted()],
    )
    server.register_tool(
        ServiceSpec(service=lambda: time.sleep(0.5), permissions=OPEN),
        name="invoices.slow",
        dispatch_timeout=0.01,
    )
    return server


def _call(server: MCPServer, name: str, *, is_async: bool) -> Any:
    params = {"name": name, "arguments": {}}
    if is_async:
        return async_to_sync(handle_tools_call_async)(params, _context(server))
    return handle_tools_call(params, _context(server))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_kernel_service_tool_announces_its_invalidations(is_async: bool) -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(subscription_broker=broker)
    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://1")}))
    _call(server, "invoices.touch", is_async=is_async)
    assert queue.get_nowait()["params"]["uri"] == "invoices://1"


@pytest.mark.django_db
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_kernel_tools_result_ceiling_refuses_an_oversized_result(is_async: bool) -> None:
    out = _call(_server(), "invoices.big", is_async=is_async)
    assert "over this server's 200 byte ceiling" in tool_error(out)["message"]


@pytest.mark.django_db
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_kernel_tools_rate_limit_refuses_the_call(is_async: bool) -> None:
    out = _call(_server(), "invoices.limited", is_async=is_async)
    assert (out.code, out.message, out.data) == (-32005, "Rate limit exceeded", {"retryAfter": 30})


@pytest.mark.django_db(transaction=True)
def test_a_kernel_tools_dispatch_deadline_ends_the_wait() -> None:
    out = _call(_server(), "invoices.slow", is_async=True)
    assert "dispatch deadline" in tool_error(out)["message"]


@pytest.mark.django_db
@pytest.mark.parametrize("is_async", [False, True], ids=["call_tool", "acall_tool"])
def test_an_anonymous_in_process_caller_reaches_a_kernel_tool(is_async: bool) -> None:
    """``user=None`` dispatches for an ``AnonymousUser`` on both routes, where the
    kernel's ``adispatch`` would refuse ``principal=None`` outright."""
    seen: list[Any] = []
    server = MCPServer(name="knobs", auth_backend=AllowAnyBackend())
    server.register_tool(
        ServiceSpec(service=lambda *, user: seen.append(user), permissions=OPEN),
        name="invoices.seen",
        description="Record the caller.",
    )
    if is_async:
        out = async_to_sync(server.acall_tool)("invoices.seen", {}, user=None)
        assert "isError" not in out
    else:
        assert server.call_tool("invoices.seen", {}, user=None).is_error is False
    assert [type(user) for user in seen] == [AnonymousUser]


@pytest.mark.django_db
def test_acall_tool_carries_its_token_on_the_request_it_synthesises() -> None:
    """``acall_tool`` reaches the wire handler with the ``HttpRequest`` it builds,
    so the kernel path's ``request.mcp_token`` always has a request to land on."""
    seen: list[Any] = []
    server = MCPServer(name="knobs", auth_backend=AllowAnyBackend())
    server.register_tool(
        ServiceSpec(service=lambda *, request: seen.append(request.mcp_token), permissions=OPEN),
        name="invoices.seen",
        description="Record the caller's token.",
    )
    out = async_to_sync(server.acall_tool)("invoices.seen", {}, user=None, scopes=("read",))
    assert "isError" not in out
    assert [token.scopes for token in seen] == [("read",)]
