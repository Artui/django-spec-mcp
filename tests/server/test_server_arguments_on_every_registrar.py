"""``server_arguments=`` means the same on every kernel registrar.

Each route a kernel tool can be registered by takes it: ``register_tool`` and a
``register_tools`` override. Each route is asked both halves, for a selector
and a service: a bound value reaches the callable, and a binding that cannot
hold is refused at registration naming the tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend


@dataclass
class _Note:
    project: str
    note: str


@dataclass
class _Card:
    project: str


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _spec(kind: str, seen: list[Any]) -> Any:
    def card(*, project: str = "none") -> _Card:
        seen.append(project)
        return _Card(project=project)

    def add_note(*, project: str, note: str) -> _Note:
        seen.append({"project": project, "note": note})
        return _Note(project=project, note=note)

    if kind == "selector":
        return SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=card,
            reads=Parameters([Parameter("project", "string", required=False)]),
            presenter=DataclassPresenter(_Card),
            permissions=[Unrestricted()],
        )
    return ServiceSpec(
        service=add_note,
        validator=DataclassValidator(_Note),
        presenter=DataclassPresenter(_Note),
        permissions=[Unrestricted()],
    )


def _register(server: MCPServer, *, route: str, spec: Any, bind: ServerArguments) -> None:
    if route == "register_tool":
        server.register_tool(spec, name="op", description="d", server_arguments=bind)
    else:
        server.register_tools(
            SpecRegistry([RegisteredSpec(name="op", spec=spec)]),
            overrides={"op": {"description": "d", "server_arguments": bind}},
        )


routes = pytest.mark.parametrize("route", ["register_tool", "register_tools"])
kinds = pytest.mark.parametrize("kind", ["selector", "service"])


@pytest.mark.django_db
@kinds
@routes
def test_a_bound_value_reaches_the_callable(route: str, kind: str) -> None:
    seen: list[Any] = []
    server = _server()
    _register(
        server, route=route, spec=_spec(kind, seen), bind=ServerArguments(values={"project": "7"})
    )
    arguments = {} if kind == "selector" else {"note": "hi"}
    result = server.call_tool("op", arguments, user=None)
    assert result.is_error is False, result
    assert seen == (["7"] if kind == "selector" else [{"project": "7", "note": "hi"}])


@kinds
@routes
def test_a_binding_that_cannot_hold_is_refused_naming_the_tool(route: str, kind: str) -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=r"'op'.*tenant"):
        _register(
            server, route=route, spec=_spec(kind, []), bind=ServerArguments(values={"tenant": 1})
        )
    assert "op" not in server.tools
