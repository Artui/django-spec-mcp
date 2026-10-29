"""Per-binding rate limits on kernel tools and resources, and on prompts, over both handlers.

Asks of specs registered with ``register_tool`` / ``register_resource``:
``rate_limits=`` is a per-binding knob, and a limiter reporting the limit spent
answers ``-32005`` with its ``retryAfter`` before the spec runs.
``consume_rate_limits`` and the prompt cases name no spec.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django_service_specs import SelectorKind, SelectorSpec, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.utils import consume_rate_limits
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from tests.utils import call_context

OPEN = [Unrestricted()]


class _AlwaysDeny:
    """Rate limiter that always reports the limit is exhausted."""

    def __init__(self, retry: int = 30) -> None:
        self._retry = retry

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        return self._retry


class _AlwaysAllow:
    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        return None


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _both(sync: Callable[..., Any], asynchronous: Callable[..., Any]) -> Any:
    return pytest.mark.parametrize(
        "handle", [sync, async_to_sync(asynchronous)], ids=["sync", "async"]
    )


# ---------- consume_rate_limits ----------


def test_consume_rate_limits_returns_none_when_all_allow() -> None:
    limits = (_AlwaysAllow(), _AlwaysAllow())
    assert consume_rate_limits(limits, HttpRequest(), TokenInfo(user=None)) is None


def test_consume_rate_limits_short_circuits_on_first_denial() -> None:
    limits = (_AlwaysDeny(retry=5), _AlwaysAllow())
    assert consume_rate_limits(limits, HttpRequest(), TokenInfo(user=None)) == 5


def test_consume_rate_limits_empty_tuple_allows() -> None:
    assert consume_rate_limits((), HttpRequest(), TokenInfo(user=None)) is None


# ---------- tools/call, resources/read, prompts/get ----------


@_both(handle_tools_call, handle_tools_call_async)
def test_a_kernel_tool_answers_rate_limited_before_it_runs(handle: Callable[..., Any]) -> None:
    ran: list[int] = []
    server = _server()
    server.register_tool(
        ServiceSpec(service=lambda: ran.append(1), permissions=OPEN, atomic=False),
        name="t",
        rate_limits=[_AlwaysDeny(retry=42)],
    )
    out = handle({"name": "t", "arguments": {}}, call_context(server))
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32005, {"retryAfter": 42})
    assert ran == []


@_both(handle_resources_read, handle_resources_read_async)
def test_a_kernel_resource_answers_rate_limited_before_it_reads(
    handle: Callable[..., Any],
) -> None:
    ran: list[int] = []
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=lambda: ran.append(1) or {}, permissions=OPEN
        ),
        name="r",
        uri_template="r://",
        rate_limits=[_AlwaysDeny(retry=7)],
    )
    out = handle({"uri": "r://"}, call_context(server))
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32005, {"retryAfter": 7})
    assert ran == []


@_both(handle_prompts_get, handle_prompts_get_async)
def test_a_prompt_answers_rate_limited(handle: Callable[..., Any]) -> None:
    server = _server()
    server.prompts.register(
        PromptBinding(
            name="p", description=None, render=lambda **_: "x", rate_limits=(_AlwaysDeny(retry=3),)
        )
    )
    out = handle({"name": "p", "arguments": {}}, call_context(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32005
