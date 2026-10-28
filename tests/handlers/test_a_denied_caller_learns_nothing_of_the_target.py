"""A caller a kernel spec's permission denies learns nothing about the target it named.

A denied caller is answered ``-32006`` alike for a row that exists and one that
does not, the class-level check is asked once and not again on the resolved
row, the object-level check still refuses a row, a list target is not judged
row by row, and a denied caller is refused before its input is validated. The
kernel's ``dispatch`` fixes that order itself - class-level authorization,
target resolution, object-level authorization, then validation - so these ask
it of kernel ``ServiceSpec`` and ``SelectorSpec`` tools on every route.

A denied caller is also not told which argument it left out. The kernel's ``dispatch`` runs its shape check before
authorization, so the transport asks the spec's class-level checks itself
first (``admit_kernel_call``), on every route, and a caller they deny missing
a required read is refused for its permission. A caller they admit is still
refused for its shape, and neither reaches the lookup.

A kernel spec declares ``permissions=None``, guarded by nothing, only under
``REQUIRE_TOOL_PERMISSIONS=False``, which ``test_kernel_unguarded.py`` asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import tool_error

_ROUTES = ["handler", "async_handler", "acall_tool", "call_tool"]
_KINDS = ["service", "selector"]

# Row 1 exists and row 2 does not.
_ROWS: dict[int, dict[str, Any]] = {1: {"id": 1}}
_PK = Parameters([Parameter("pk", "integer", required=True)])


class _Check(PermissionCheck):
    """Answers each question as configured, counting how often each was asked."""

    def __init__(self, *, admits: bool = True, admits_rows: bool = True) -> None:
        self.admits = admits
        self.admits_rows = admits_rows
        self.asked: list[str] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.asked.append("class")
        return self.admits

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        self.asked.append("object")
        return self.admits_rows

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any) -> bool:
        self.asked.append("scope")
        return True


class _Lookup:
    """The target selector, recording every ``pk`` it was asked to find."""

    def __init__(self) -> None:
        self.looked_up: list[int] = []

    def __call__(self, *, pk: int) -> dict[str, Any] | None:
        self.looked_up.append(pk)
        return _ROWS.get(pk)


@dataclass
class _Note:
    note: str

    def __post_init__(self) -> None:
        if self.note == "rejected":
            raise ValueError("This note is rejected.")


def _server(kind: str, check: PermissionCheck, lookup: _Lookup) -> MCPServer:
    """One tool named ``tool``, of ``kind``, whose target ``lookup`` finds by ``pk``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    if kind == "service":
        spec: Any = ServiceSpec(
            service=lambda instance: {"touched": instance["id"]},
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lookup, reads=_PK
            ),
            validator=DataclassValidator(_Note),
            permissions=[check],
        )
    else:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=lookup, reads=_PK, permissions=[check]
        )
    server.register_tool(spec, name="tool", description="Touch a row.")
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


async def _via(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``tool`` through ``route`` answers, a denial read as ``-32006``."""
    if route == "call_tool":
        try:
            result: Any = await sync_to_async(server.call_tool)("tool", arguments, user=None)
        except NotPermitted:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        return result.to_dict()
    if route == "acall_tool":
        return await server.acall_tool("tool", arguments, user=None)
    params: dict[str, Any] = {"name": "tool", "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


def _arguments(kind: str, pk: int) -> dict[str, Any]:
    return {"pk": pk, "note": "x"} if kind == "service" else {"pk": pk}


def _forbidden(out: Any) -> bool:
    return isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_denied_caller_is_answered_alike_for_a_row_that_exists_and_one_that_does_not(
    route: str, kind: str
) -> None:
    lookup = _Lookup()
    server = _server(kind, _Check(admits=False), lookup)

    found = await _via(server, route, _arguments(kind, 1))
    missing = await _via(server, route, _arguments(kind, 2))

    assert _forbidden(found), found
    assert _forbidden(missing), missing
    assert lookup.looked_up == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_denied_caller_missing_a_read_is_refused_before_its_shape_and_any_lookup(
    route: str, kind: str
) -> None:
    # A shape answer describes a tool the caller may not call, which the
    # listing hides from it.
    lookup = _Lookup()
    check = _Check(admits=False)
    server = _server(kind, check, lookup)

    out = await _via(server, route, {"note": "x"} if kind == "service" else {})

    assert _forbidden(out), out
    assert check.asked == ["class"]
    assert lookup.looked_up == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_an_admitted_call_missing_a_read_is_refused_for_its_shape_before_any_lookup(
    route: str, kind: str
) -> None:
    lookup = _Lookup()
    check = _Check()
    server = _server(kind, check, lookup)

    out = await _via(server, route, {"note": "x"} if kind == "service" else {})

    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert "pk" in str(error)
    assert check.asked == ["class"]
    assert lookup.looked_up == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("pk", [1, 2], ids=["found", "missing"])
async def test_the_class_level_check_is_asked_once_whatever_the_lookup_finds(
    kind: str, pk: int
) -> None:
    # Once for a missing row as for a found one, so how often the check is
    # asked says nothing about whether the row exists; only a found row is
    # then judged at the object level.
    check = _Check()
    server = _server(kind, check, _Lookup())

    await _via(server, "handler", _arguments(kind, pk))

    assert check.asked == (["class", "object"] if pk == 1 else ["class"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_the_object_level_check_still_refuses_a_row(route: str, kind: str) -> None:
    server = _server(kind, _Check(admits_rows=False), _Lookup())

    out = await _via(server, route, _arguments(kind, 1))

    assert _forbidden(out), out


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
async def test_a_permitted_row_is_served(kind: str) -> None:
    out = await _via(_server(kind, _Check(), _Lookup()), "handler", _arguments(kind, 1))

    assert not out.get("isError"), out
    assert out["structuredContent"] == ({"touched": 1} if kind == "service" else {"id": 1})


@pytest.mark.django_db(transaction=True)
async def test_a_list_target_is_judged_as_a_set_not_row_by_row() -> None:
    check = _Check(admits_rows=False)
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST, selector=lambda: [{"id": 1}, {"id": 2}], permissions=[check]
        ),
        name="tool",
    )

    out = await _via(server, "handler", {})

    assert out["structuredContent"] == [{"id": 1}, {"id": 2}]
    assert check.asked == ["class", "scope"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_denied_caller_is_refused_before_its_input_is_validated(route: str) -> None:
    # The note passes the shape check and fails the validator: a validator
    # answering first would tell a denied caller what the input is held to,
    # and that the row resolved.
    server = _server("service", _Check(admits=False), _Lookup())

    out = await _via(server, route, {"pk": 1, "note": "rejected"})
    # Sized so the validator is what refuses an admitted caller the same call.
    admitted = await _via(
        _server("service", _Check(), _Lookup()), route, {"pk": 1, "note": "rejected"}
    )

    assert _forbidden(out), out
    assert tool_error(admitted)["type"] == "validation_error"
    assert "This note is rejected." in str(tool_error(admitted))
