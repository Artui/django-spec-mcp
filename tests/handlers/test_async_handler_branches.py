"""The async handlers' own branches, on kernel tools and resources.

Asks of specs registered with ``register_tool`` / ``register_resource``,
through ``handle_tools_call_async``, ``handle_resources_read_async`` and
``adispatch``: the guards name a malformed
call, a refusal keeps its shape, an ``async def`` service, output selector and
resource selector are awaited on the loop rather than sent to a thread, and the
sync handler table is sent off the loop while its answer still reaches the wire.
The branches the async handler shares with the sync one are asked of both in
``test_handler_branches.py``.

A kernel Validator reads the call's context off its ``ValidationContext``,
asked here, and a kernel Presenter takes no per-call context.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
    ValidationContext,
    Validator,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.async_dispatch import adispatch
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.utils import tool_error

OPEN = [Unrestricted()]


@dataclass
class _In:
    x: int


class _ContextSeen(Validator):
    """A Validator recording the ``call_context`` its ``ValidationContext`` carries."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def parameters(self) -> Parameters:
        return Parameters([Parameter("x", "integer", required=True)])

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        self.seen = dict(context.call_context)
        return dict(arguments)


class _LoopSniffingTaskStore:
    """A task store that records whether it was read from the event loop."""

    def __init__(self) -> None:
        self.on_loop: bool | None = None

    def get(self, task_id: str) -> Any:  # noqa: ARG002
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.on_loop = False
        else:
            self.on_loop = True
        return None


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=server.config,
    )


def _one(spec: Any, **knobs: Any) -> MCPServer:
    server = _server()
    server.register_tool(spec, name="t", **knobs)
    return server


async def _call(server: MCPServer, arguments: dict[str, Any] | None = None) -> Any:
    return await handle_tools_call_async({"name": "t", "arguments": arguments or {}}, _ctx(server))


# ---------- tools/call guards ----------


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (None, "tools/call params must be an object"),
        ({}, "'name' is required and must be a string"),
        ({"name": 7}, "'name' is required and must be a string"),
        ({"name": "nope"}, "Unknown tool: 'nope'"),
        ({"name": "t", "arguments": [1, 2]}, "'arguments' must be an object"),
    ],
    ids=["non-dict-params", "missing-name", "non-string-name", "unknown-tool", "list-arguments"],
)
async def test_a_malformed_call_is_invalid_params(params: Any, message: str) -> None:
    server = _one(ServiceSpec(service=lambda: None, permissions=OPEN))
    out = await handle_tools_call_async(params, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.message) == (-32602, message)


async def test_an_invalid_input_is_a_validation_error() -> None:
    server = _one(
        ServiceSpec(service=lambda *, x: x, validator=DataclassValidator(_In), permissions=OPEN)
    )
    error = tool_error(await _call(server, {"x": "not-a-number"}))
    assert error["type"] == "validation_error"
    assert set(error["detail"]) == {"x"}


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (ServiceValidationError({"field": ["bad"]}), "validation_error"),
        (ServiceError("boom"), "service_error"),
    ],
    ids=["validation", "service"],
)
async def test_a_raised_service_error_is_a_tool_error(raised: Exception, expected: str) -> None:
    def svc() -> None:
        raise raised

    error = tool_error(await _call(_one(ServiceSpec(service=svc, permissions=OPEN, atomic=False))))
    assert error["type"] == expected


async def test_a_scope_gate_denies_the_async_call() -> None:
    server = _one(ServiceSpec(service=lambda: None, permissions=OPEN), scopes=("scope:x",))
    out = await _call(server)
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32006, {"requiredScopes": ["scope:x"]})


# ---------- awaited on the loop ----------


async def test_an_async_service_is_awaited_on_the_loop() -> None:
    """A coroutine-function service is awaited directly, not wrapped in a thread."""
    loop_thread = threading.get_ident()

    async def aproduce() -> dict[str, Any]:
        return {"async": True, "on_loop": threading.get_ident() == loop_thread}

    out = await _call(_one(ServiceSpec(service=aproduce, permissions=OPEN, atomic=False)))
    assert out["structuredContent"] == {"async": True, "on_loop": True}


async def test_an_async_output_selector_reshapes_the_result() -> None:
    async def shape(*, result: dict[str, Any]) -> dict[str, Any]:
        return {"shaped": result["raw"]}

    server = _one(
        ServiceSpec(
            service=lambda: {"raw": True},
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=shape, permissions=OPEN
            ),
            permissions=OPEN,
            atomic=False,
        )
    )
    assert (await _call(server))["structuredContent"] == {"shaped": True}


async def test_a_validator_reads_the_calls_context() -> None:
    """The Validator is handed the call's context, carrying the request and the
    tool's name."""
    validator = _ContextSeen()
    server = _one(
        ServiceSpec(
            service=lambda *, x: {"got": x}, validator=validator, permissions=OPEN, atomic=False
        )
    )
    out = await _call(server, {"x": 1})
    assert out["structuredContent"] == {"got": 1}
    assert validator.seen["action"] == "t"
    assert isinstance(validator.seen["request"], HttpRequest)


# ---------- resources/read ----------


def _resource_server(selector: Any, **knobs: Any) -> MCPServer:
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            reads=Parameters([Parameter("pk", "string", required=True)]),
            permissions=OPEN,
        ),
        name="r",
        uri_template="r://{pk}",
        **knobs,
    )
    return server


@pytest.mark.parametrize(
    ("params", "code"),
    [(None, -32602), ({}, -32602), ({"uri": "nope://x"}, -32002)],
    ids=["non-dict-params", "missing-uri", "unknown-uri"],
)
async def test_a_malformed_or_unknown_read_is_refused(params: Any, code: int) -> None:
    out = await handle_resources_read_async(
        params, _ctx(_resource_server(lambda *, pk: {"pk": pk}))
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == code


async def test_a_read_denied_by_its_scope_gate() -> None:
    server = _resource_server(lambda *, pk: {"pk": pk}, scopes=("scope:x",))
    out = await handle_resources_read_async({"uri": "r://1"}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32006, {"requiredScopes": ["scope:x"]})


async def test_an_async_resource_selector_is_awaited() -> None:
    async def aselector(*, pk: str) -> dict[str, Any]:
        return {"async": True, "pk": pk}

    out = await handle_resources_read_async({"uri": "r://9"}, _ctx(_resource_server(aselector)))
    assert '"async": true' in out["contents"][0]["text"]
    assert '"pk": "9"' in out["contents"][0]["text"]


# ---------- adispatch ----------


async def test_adispatch_routes_prompts_get_to_the_async_handler() -> None:
    server = _server()
    server.prompts.register(PromptBinding(name="p", description=None, render=lambda **_: "ok"))
    out = await adispatch("prompts/get", {"name": "p", "arguments": {}}, _ctx(server))
    assert out["messages"][0]["content"]["text"] == "ok"


async def test_the_sync_handler_table_does_not_run_on_the_event_loop() -> None:
    """``tasks/get`` reads the task store, and the default store is the Django
    cache, which as ``DatabaseCache`` reaches ``django.db`` and raises
    ``SynchronousOnlyOperation`` on the loop."""
    store = _LoopSniffingTaskStore()
    context = replace(
        _ctx(_server()),
        tasks=store,
        client_capabilities={"extensions": {TASKS_EXTENSION_ID: {}}},
    )
    result = await adispatch("tasks/get", {"taskId": "nope"}, context)
    # The handler ran and answered; what matters is the thread it ran on.
    assert isinstance(result, JsonRpcError)
    assert store.on_loop is False


async def test_the_fall_through_still_returns_the_handler_result() -> None:
    """The thread hop must be invisible to the wire: ``ping`` still answers."""
    assert await adispatch("ping", None, _ctx(_server())) == {}
