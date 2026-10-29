"""``MCPServer.call_tool`` on kernel specs: the in-process, spec-core invocation surface.

Asks of tools registered with ``register_tool``: a call through the
kernel's ``dispatch`` off the HTTP path returns a :class:`ToolResult`, a
business failure or a missing instance comes back as an ``isError`` result, and
a permission fault or an unknown name propagates for the caller to map.

A denied call raises the kernel's ``NotPermitted``. A kernel retrieve declares
the read it takes, ``pk`` here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import Http404
from django_service_specs import (
    UNSET,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.protocol.types.tool_result import ToolResult
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]
PK = Parameters([Parameter("pk", "integer", required=True)])


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _one(spec: Any, name: str, **registration: Any) -> MCPServer:
    """A server with ``spec`` registered as the one tool ``name``."""
    server = _server()
    server.register_tool(spec, name=name, **registration)
    return server


def _error(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.content[0].text)["error"]


class _DenyAll(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


class _DenyObject(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        return False


# ---------- success paths ----------


def test_a_service_tool_returns_a_tool_result() -> None:
    server = _server()
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=OPEN, atomic=False),
        name="things.create",
    )
    # No argument: the spec declares none, so ``REJECT`` would refuse one.
    result = server.call_tool("things.create", {}, user=None)
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.structured_content == {"ok": True}


@pytest.mark.parametrize(
    ("spec", "arguments", "rendered"),
    [
        (
            SelectorSpec(
                kind=SelectorKind.LIST, selector=lambda: [{"a": 1}, {"a": 2}], permissions=OPEN
            ),
            {},
            [{"a": 1}, {"a": 2}],
        ),
        (
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, pk: {"a": pk},
                reads=PK,
                permissions=OPEN,
            ),
            {"pk": 1},
            {"a": 1},
        ),
    ],
    ids=["list-renders-many", "retrieve-renders-one-instance"],
)
def test_a_selector_renders_what_its_kind_returns(
    spec: SelectorSpec, arguments: dict[str, Any], rendered: Any
) -> None:
    result = _one(spec, "things.read").call_tool("things.read", arguments, user=None)

    assert result.structured_content == rendered


def test_an_allow_none_retrieve_returns_an_empty_result_not_an_error() -> None:
    server = _server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: None,
            reads=PK,
            allow_none=True,
            permissions=OPEN,
        ),
        name="things.maybe",
    )
    result = server.call_tool("things.maybe", {"pk": 9}, user=None)
    assert result.is_error is False
    assert result.structured_content == {}


def test_include_structured_content_false_omits_the_structured_field() -> None:
    server = _server()
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=OPEN, atomic=False),
        name="things.quiet",
        include_structured_content=False,
        # The MCP spec forbids advertising outputSchema while omitting
        # structuredContent, so drop the schema too.
        include_output_schema=False,
    )
    result = server.call_tool("things.quiet", user=None)
    assert result.structured_content is UNSET
    assert "structuredContent" not in result.to_dict()
    assert json.loads(result.content[0].text) == {"ok": True}


# ---------- tool-level error paths (isError results) ----------


def test_a_missing_required_instance_is_a_not_found_error() -> None:
    server = _server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=lambda *, pk: None, reads=PK, permissions=OPEN
        ),
        name="things.get",
    )
    result = server.call_tool("things.get", {"pk": 404}, user=None)
    assert result.is_error is True
    assert _error(result) == {
        "message": "things.get: no matching instance found",
        "type": "not_found",
    }


@pytest.mark.parametrize("allow_none", [False, True], ids=["plain", "allow-none"])
def test_a_list_selectors_http404_is_not_found_in_process(allow_none: bool) -> None:
    """The wire's wording for a ``LIST``, on the route django-ag-ui calls."""

    def missing_parent() -> Any:
        raise Http404("no parent")

    server = _server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=missing_parent,
            allow_none=allow_none,
            permissions=OPEN,
        ),
        name="things.list",
    )
    result = server.call_tool("things.list", {}, user=None)
    assert result.is_error is True
    assert _error(result) == {"message": "things.list: not found", "type": "not_found"}


def test_a_write_whose_instance_selector_raises_http404_is_not_found_unrun() -> None:
    """A service tool's ``not_found`` keeps the instance wording: its binding's
    ``kind`` is no ``LIST``, so ``not_found_result`` words a missed instance."""
    ran: list[Any] = []

    def missing(*, pk: int) -> Any:
        raise Http404("gone")

    server = _server()
    server.register_tool(
        ServiceSpec(
            service=lambda **kwargs: ran.append(kwargs),
            atomic=False,
            permissions=OPEN,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=missing, reads=PK, permissions=OPEN
            ),
        ),
        name="things.update",
    )
    result = server.call_tool("things.update", {"pk": 1}, user=None)
    assert result.is_error is True
    assert _error(result) == {
        "message": "things.update: no matching instance found",
        "type": "not_found",
    }
    assert ran == []


def test_an_undeclared_argument_is_a_validation_error_not_a_fault() -> None:
    """A kernel spec declares its reads, so an argument it does not declare
    (``{"x": 1}`` here) is refused under ``REJECT`` as the wire refuses it, in
    the result rather than raised."""
    server = _server()
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=OPEN, atomic=False), name="t.create"
    )
    result = server.call_tool("t.create", {"x": 1}, user=None)
    assert result.is_error is True
    assert _error(result)["detail"] == {"x": ["Unknown argument."]}


@pytest.mark.parametrize(
    ("raised", "error_type"),
    [
        (ServiceValidationError("bad input"), "validation_error"),
        (ServiceError("boom"), "service_error"),
    ],
    ids=["validation", "service"],
)
def test_a_service_error_becomes_a_tool_error(raised: Exception, error_type: str) -> None:
    def svc() -> Any:
        raise raised

    result = _one(ServiceSpec(service=svc, permissions=OPEN, atomic=False), "t.create").call_tool(
        "t.create", {}, user=None
    )
    assert result.is_error is True
    assert _error(result)["type"] == error_type
    assert _error(result)["message"] == str(raised)


# ---------- protocol faults (propagate) ----------


def test_a_spec_permission_denial_propagates() -> None:
    # A kernel check refuses with the kernel's ``NotPermitted``, which the wire
    # maps to its own code. It is a fault the caller maps, not a tool result.
    server = _server()
    server.register_tool(
        ServiceSpec(service=lambda: {}, permissions=[_DenyAll()], atomic=False), name="t.create"
    )
    with pytest.raises(NotPermitted):
        server.call_tool("t.create", user=None)


def test_a_selector_spec_permission_denial_propagates_before_the_selector_runs() -> None:
    ran: list[bool] = []

    def _selector(*, pk: int) -> Any:
        ran.append(True)
        return {"leaked": True}

    server = _server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_selector, reads=PK, permissions=[_DenyAll()]
        ),
        name="t.secret",
    )
    with pytest.raises(NotPermitted):
        server.call_tool("t.secret", {"pk": 1}, user=None)
    assert ran == []


@pytest.mark.django_db
def test_a_selector_object_permission_denial_propagates() -> None:
    """The object-level half: the kernel asks ``has_object_permission`` of the
    row the retrieve resolved, so a denial on it propagates too."""
    invoice = Invoice.objects.create(number="A", amount_cents=1)
    server = _server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: Invoice.objects.filter(pk=pk),
            reads=PK,
            permissions=[_DenyObject()],
        ),
        name="t.secret",
    )
    with pytest.raises(NotPermitted):
        server.call_tool("t.secret", {"pk": invoice.pk}, user=None)


def test_an_unknown_tool_name_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="nope"):
        _server().call_tool("nope", user=None)
