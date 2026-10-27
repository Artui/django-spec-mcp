"""The kernel branches of ``handle_tools_call`` and its async sibling, called directly.

Asks of tools and resources registered with ``register_tool`` /
``register_resource`` what the original, kept beside this file as
``test_handler_branches_drfs.py``, asks of hand-built drf-services bindings, and
asks it of both handlers through the ``handle`` fixture: a validated dataclass
input reaches the service, a service returning ``None`` still serves an object,
a validation refusal keeps its detail, a scope gate or a spec permission denies
with ``-32006``, an output selector reshapes the result, the structured-content
and output-schema knobs answer per binding and server-wide, and the request-shape
guards name a malformed ``arguments`` or ``outputFormat`` before the tool runs.

Where the kernel answers differently from the original:

- ``ArgumentBinding.SPREAD_AUTHOR_WINS`` has no kernel counterpart, because a
  kernel spec always spreads its declared reads into the service's keywords.
  The question it carried, whether a caller can name a reserved pool seed, is
  asked of ``user`` under both ``unknown_arguments`` policies.
- ``check_permissions`` is asked directly, as the original asks it: it judges a
  binding's transport permissions on every path, the kernel scope gate's
  included, and names no spec.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import check_permissions
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import tool_error

OPEN = [Unrestricted()]

Handle = Callable[..., Any]


@dataclass
class _BareDC:
    name: str
    qty: int = 0


@dataclass
class _Out:
    a: int


class _DenySpec(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


def _server(config: MCPConfig | None = None) -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, config=config)


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


def _sync_tools(params: Any, ctx: MCPCallContext) -> Any:
    return handle_tools_call(params, ctx)


def _async_tools(params: Any, ctx: MCPCallContext) -> Any:
    return async_to_sync(handle_tools_call_async)(params, ctx)


@pytest.fixture(params=[_sync_tools, _async_tools], ids=["sync", "async"])
def handle(request: pytest.FixtureRequest) -> Handle:
    """``tools/call`` through the sync handler, or through the async one."""
    return request.param


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def read(request: pytest.FixtureRequest) -> Handle:
    """``resources/read`` through the sync handler, or through the async one."""
    if request.param:
        return lambda params, ctx: async_to_sync(handle_resources_read_async)(params, ctx)
    return handle_resources_read


def _call(handle: Handle, server: MCPServer, arguments: Any = None, **params: Any) -> Any:
    return handle({"name": "t", "arguments": arguments or {}, **params}, _ctx(server))


def _one(spec: Any, config: MCPConfig | None = None, **knobs: Any) -> MCPServer:
    server = _server(config)
    server.register_tool(spec, name="t", **knobs)
    return server


def test_a_dataclass_input_reaches_the_service_as_its_keywords(handle: Handle) -> None:
    server = _one(
        ServiceSpec(
            service=lambda *, name, qty: {"name": name, "qty": qty},
            validator=DataclassValidator(_BareDC),
            permissions=OPEN,
            atomic=False,
        )
    )
    out = _call(handle, server, {"name": "x", "qty": 3})
    assert out["structuredContent"] == {"name": "x", "qty": 3}


def test_a_service_returning_none_still_serves_an_object(handle: Handle) -> None:
    """Tools that return None must still produce an object as ``structuredContent``."""
    server = _one(ServiceSpec(service=lambda: None, permissions=OPEN, atomic=False))
    assert _call(handle, server)["structuredContent"] == {}


def test_a_service_validation_error_keeps_its_detail(handle: Handle) -> None:
    def svc() -> None:
        raise ServiceValidationError({"field": ["bad"]})

    error = tool_error(
        _call(handle, _one(ServiceSpec(service=svc, permissions=OPEN, atomic=False)))
    )
    assert error["type"] == "validation_error"
    assert error["detail"] == {"field": ["bad"]}


def test_a_scope_gate_denies_with_the_required_scopes(handle: Handle) -> None:
    ran: list[int] = []
    server = _one(
        ServiceSpec(service=lambda: ran.append(1), permissions=OPEN, atomic=False),
        scopes=("scope:x",),
    )
    out = _call(handle, server)
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32006, {"requiredScopes": ["scope:x"]})
    assert ran == []


def test_a_spec_permission_denies_as_forbidden(handle: Handle) -> None:
    out = _call(handle, _one(ServiceSpec(service=lambda: None, permissions=[_DenySpec()])))
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32006, None)


def test_an_output_selector_reshapes_the_result(handle: Handle) -> None:
    server = _one(
        ServiceSpec(
            service=lambda: {"raw": True},
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, result: {"shaped": result["raw"]},
                permissions=OPEN,
            ),
            permissions=OPEN,
            atomic=False,
        )
    )
    assert _call(handle, server)["structuredContent"] == {"shaped": True}


def test_a_resource_read_passes_through_with_no_presenter(read: Handle) -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: {"pk": pk},
            reads=Parameters([Parameter("pk", "string", required=True)]),
            permissions=OPEN,
        ),
        name="r",
        uri_template="r://{pk}",
    )
    out = read({"uri": "r://7"}, _ctx(server))
    assert '"pk": "7"' in out["contents"][0]["text"]


def test_a_resource_read_denied_by_its_scope_gate(read: Handle) -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=dict, permissions=OPEN),
        name="r",
        uri_template="r://",
        scopes=("scope:x",),
    )
    out = read({"uri": "r://"}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert (out.code, out.data) == (-32006, {"requiredScopes": ["scope:x"]})


class _Judge:
    """A transport permission answering ``allowed``, naming ``scopes`` when it denies."""

    def __init__(self, allowed: bool, scopes: list[str]) -> None:
        self.allowed = allowed
        self.scopes = scopes

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return self.allowed

    def required_scopes(self) -> list[str]:
        return self.scopes


@pytest.mark.parametrize(
    ("permissions", "answer"),
    [
        # A denial naming no scope serves an empty list, not ``None``.
        ((_Judge(False, []),), (False, [])),
        ((_Judge(True, ["unused"]),), (True, [])),
        ((), (True, [])),
    ],
    ids=["denied-naming-no-scope", "all-allow", "none"],
)
def test_check_permissions(permissions: tuple[Any, ...], answer: tuple[bool, list[str]]) -> None:
    assert check_permissions(permissions, HttpRequest(), TokenInfo(user=None)) == answer


# ---------- structuredContent and outputSchema ----------


def _a_spec() -> ServiceSpec:
    return ServiceSpec(
        service=lambda: _Out(a=1),
        presenter=DataclassPresenter(_Out),
        permissions=OPEN,
        atomic=False,
    )


def test_structured_content_is_omitted_when_the_server_disables_it(handle: Handle) -> None:
    # Disabling structuredContent also requires disabling outputSchema:
    # advertising the schema while suppressing the content is a spec
    # violation that ``resolve_structured_output`` rejects.
    config = build_mcp_config(include_structured_content=False, include_output_schema=False)
    out = _call(handle, _one(_a_spec(), config))
    assert "structuredContent" not in out
    # The text rendering still carries the payload.
    assert '"a": 1' in out["content"][0]["text"]


def test_a_binding_forces_structured_content_off_with_the_server_on(handle: Handle) -> None:
    config = build_mcp_config(include_structured_content=True)
    server = _one(_a_spec(), config, include_structured_content=False, include_output_schema=False)
    assert "structuredContent" not in _call(handle, server)


def test_a_binding_forces_structured_content_on_with_the_server_off(handle: Handle) -> None:
    config = build_mcp_config(include_structured_content=False, include_output_schema=False)
    server = _one(_a_spec(), config, include_structured_content=True)
    assert _call(handle, server)["structuredContent"] == {"a": 1}


def _listed(server: MCPServer) -> dict[str, Any]:
    return handle_tools_list(None, _ctx(server))["tools"][0]


def test_tools_list_drops_the_output_schema_a_binding_disables() -> None:
    assert "outputSchema" not in _listed(_one(_a_spec(), include_output_schema=False))


def test_tools_list_drops_every_output_schema_the_server_disables(handle: Handle) -> None:
    server = _one(_a_spec(), build_mcp_config(include_output_schema=False))
    assert "outputSchema" not in _listed(server)
    # structuredContent is still emitted: that direction is spec-allowed.
    assert _call(handle, server)["structuredContent"] == {"a": 1}


def test_tools_list_emits_the_output_schema_a_presenter_declares() -> None:
    assert _listed(_one(_a_spec()))["outputSchema"]["properties"] == {"a": {"type": "integer"}}


# ---------- spreading, and the seeds a caller cannot name ----------


def test_the_declared_reads_spread_into_the_services_keywords(handle: Handle) -> None:
    server = _one(
        ServiceSpec(
            service=lambda *, project_id, expand=False: {"pid": project_id, "expand": expand},
            reads=Parameters(
                [Parameter("project_id", "string", required=True), Parameter("expand", "boolean")]
            ),
            permissions=OPEN,
            atomic=False,
        )
    )
    out = _call(handle, server, {"project_id": "p1", "expand": True})
    assert out["structuredContent"] == {"pid": "p1", "expand": True}


@pytest.mark.parametrize(
    "policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE], ids=["reject", "ignore"]
)
def test_a_caller_cannot_name_the_user_seed(handle: Handle, policy: UnknownArguments) -> None:
    """``user`` is the dispatcher's own name, so a caller sending it is refused as
    unknown under ``REJECT`` and dropped under ``IGNORE``; either way the service
    runs as the transport's principal, never as the name the caller sent."""
    received: list[Any] = []

    def svc(*, user: Any, ok: int) -> dict[str, Any]:
        received.append(user)
        return {}

    server = _one(
        ServiceSpec(
            service=svc,
            reads=Parameters([Parameter("ok", "integer", required=True)]),
            permissions=OPEN,
            atomic=False,
        ),
        unknown_arguments=policy,
    )
    out = _call(handle, server, {"user": "evil", "ok": 1})
    if policy is UnknownArguments.REJECT:
        assert tool_error(out)["detail"] == {"user": ["Unknown argument."]}
        assert received == []
    else:
        assert "isError" not in out
        assert len(received) == 1
        assert isinstance(received[0], AnonymousUser)


# ---------- request-shape guards on tools/call ----------


def _echo() -> MCPServer:
    return _one(ServiceSpec(service=lambda: {"ok": True}, permissions=OPEN, atomic=False))


@pytest.mark.parametrize("malformed", [[], 0, "", False], ids=["list", "zero", "empty", "false"])
def test_a_falsy_non_object_arguments_field_is_named_as_the_fault(
    handle: Handle, malformed: Any
) -> None:
    out = handle({"name": "t", "arguments": malformed}, _ctx(_echo()))
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert "'arguments'" in out.message


@pytest.mark.parametrize("params", [{"name": "t"}, {"name": "t", "arguments": None}])
def test_a_missing_or_null_arguments_field_still_means_no_arguments(
    handle: Handle, params: dict[str, Any]
) -> None:
    out = handle(params, _ctx(_echo()))
    assert out["structuredContent"] == {"ok": True}


def test_an_unrenderable_output_format_is_refused_before_the_tool_runs(handle: Handle) -> None:
    ran: list[int] = []
    server = _one(ServiceSpec(service=lambda: ran.append(1), permissions=OPEN, atomic=False))
    out = _call(handle, server, outputFormat="xml")
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert "outputFormat" in out.message
    assert ran == []


@pytest.mark.parametrize("requested", ["json", "toon", "auto", ""])
def test_a_supported_or_empty_output_format_passes_the_guard(
    handle: Handle, requested: str
) -> None:
    """An empty format is read as unsupplied, as the kernel path's
    ``output_format or binding.output_format`` reads it."""
    out = _call(handle, _echo(), outputFormat=requested)
    assert "isError" not in out
