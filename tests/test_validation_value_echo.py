"""Coverage for ``SPEC_MCP['INCLUDE_VALIDATION_VALUE']``, on kernel tools.

The setting is opt-in: when True, ``value`` echoes the offending ``arguments``
dict back to the client, beside ``detail`` in a ``validation_error`` result's
``error`` object on ``tools/call``, in the ``-32602`` ``data`` on ``prompts/get``.
Both states are exercised at every call site: sync and async, a kernel tool's
two validation refusals, and prompts/get's missing required arguments.

A kernel tool's two refusals are the kernel's ``InvalidArguments``, from the
shape check before its Validator runs, and a ``ServiceValidationError`` the
service raises. The prompt cases build no spec.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
)

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.prompt_registry import PromptRegistry
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import tool_error


@dataclass
class _Args:
    name: str


def _ctx(tools: ToolRegistry) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools,
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


def _tools(spec: ServiceSpec) -> ToolRegistry:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="t", description="d")
    return server.tools


def _tools_with_dataclass_input() -> ToolRegistry:
    def svc(*, name: str) -> dict[str, Any]:
        return {"name": name}

    return _tools(
        ServiceSpec(service=svc, validator=DataclassValidator(_Args), permissions=[Unrestricted()])
    )


def _tools_raising_validation() -> ToolRegistry:
    # ``x`` is declared, so the call reaches the service: an undeclared key
    # would be refused by the kernel's shape check before it ran.
    def svc(*, x: int = 0) -> None:
        raise ServiceValidationError({"f": ["bad"]})

    return _tools(
        ServiceSpec(
            service=svc,
            reads=Parameters([Parameter("x", "integer")]),
            permissions=[Unrestricted()],
        )
    )


def _raised_by_the_service(out: Any) -> dict[str, Any]:
    error = tool_error(out)
    assert error["detail"] == {"f": ["bad"]}, error
    return error


# The sync handler, and the async one driven to a result, so each question is asked
# of both from one body.
@pytest.fixture(params=["sync", "async"])
def tools_call(request: pytest.FixtureRequest) -> Callable[[dict[str, Any], MCPCallContext], Any]:
    return handle_tools_call if request.param == "sync" else async_to_sync(handle_tools_call_async)


@pytest.fixture(params=["sync", "async"])
def prompts_get(request: pytest.FixtureRequest) -> Callable[[dict[str, Any], MCPCallContext], Any]:
    return (
        handle_prompts_get if request.param == "sync" else async_to_sync(handle_prompts_get_async)
    )


# ---------- tools/call: the kernel refusing the arguments ----------


@pytest.mark.django_db(transaction=True)
def test_tools_call_invalid_arguments_default_omits_value(settings, tools_call) -> None:
    settings.SPEC_MCP = {}
    out = tools_call(
        {"name": "t", "arguments": {"junk": True}}, _ctx(_tools_with_dataclass_input())
    )
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert "value" not in error


@pytest.mark.django_db(transaction=True)
def test_tools_call_invalid_arguments_echo(settings, tools_call) -> None:
    settings.SPEC_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = tools_call(
        {"name": "t", "arguments": {"junk": True}}, _ctx(_tools_with_dataclass_input())
    )
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["value"] == {"junk": True}


# ---------- tools/call ServiceValidationError ----------


@pytest.mark.django_db(transaction=True)
def test_tools_call_service_validation_default_omits_value(settings, tools_call) -> None:
    settings.SPEC_MCP = {}
    out = tools_call({"name": "t", "arguments": {}}, _ctx(_tools_raising_validation()))
    assert "value" not in _raised_by_the_service(out)


@pytest.mark.django_db(transaction=True)
def test_tools_call_service_validation_echo(settings, tools_call) -> None:
    settings.SPEC_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = tools_call({"name": "t", "arguments": {"x": 1}}, _ctx(_tools_raising_validation()))
    assert _raised_by_the_service(out)["value"] == {"x": 1}


# ---------- prompts/get missing required args ----------


def _server_with_required_prompt() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_prompt(
        name="echo",
        render=lambda *, who: f"hi {who}",
        arguments=[PromptArgument(name="who", required=True)],
        permissions=[Unrestricted()],
    )
    return server


def _ctx_for(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def test_prompts_get_missing_default_omits_value(settings, prompts_get) -> None:
    settings.SPEC_MCP = {}
    out = prompts_get({"name": "echo", "arguments": {}}, _ctx_for(_server_with_required_prompt()))
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"]}


def test_prompts_get_missing_echo(settings, prompts_get) -> None:
    settings.SPEC_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = prompts_get(
        {"name": "echo", "arguments": {"unrelated": "x"}}, _ctx_for(_server_with_required_prompt())
    )
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"], "value": {"unrelated": "x"}}


@pytest.mark.parametrize("malformed", [[], "", 0, False])
def test_a_falsy_non_object_prompt_arguments_field_is_named_as_the_fault(
    settings, prompts_get, malformed: Any
) -> None:
    """``[]`` is not "no arguments".

    An ``or {}`` on the field would fold every falsy value into the default,
    reaching the required-argument check as if the client had sent an empty
    object, and a prompt whose arguments are all optional would render a call
    the client never made in that shape.
    """
    settings.SPEC_MCP = {}
    out = prompts_get(
        {"name": "echo", "arguments": malformed}, _ctx_for(_server_with_required_prompt())
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert "'arguments'" in out.message


@pytest.mark.parametrize("params", [{"name": "echo"}, {"name": "echo", "arguments": None}])
def test_a_missing_or_null_prompt_arguments_field_still_means_no_arguments(
    settings, prompts_get, params: dict[str, Any]
) -> None:
    settings.SPEC_MCP = {}
    out = prompts_get(params, _ctx_for(_server_with_required_prompt()))
    # Reaches the required-argument check rather than the malformed-shape one.
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"]}
