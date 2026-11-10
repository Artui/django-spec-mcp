from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend


@dataclass
class _Echo:
    note: str
    request_is_caller_value: bool


def _echo(*, note: str, request: Any = None) -> list[_Echo]:
    return [_Echo(note=note, request_is_caller_value=request == "spoof")]


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_echo,
            reads=Parameters(
                [
                    Parameter("note", "string", required=True),
                    # Not required: an in-process ``call_tool`` has no request
                    Parameter("request", "string", reflected=True),
                ]
            ),
            presenter=DataclassPresenter(_Echo),
            permissions=[Unrestricted()],
        ),
        name="echo",
        description="Echo a note.",
    )
    return server


def _schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def test_a_reflected_read_named_request_is_not_advertised() -> None:
    # The kernel lists it as declared; the call's context is what fills it,
    schema = _schema(_server())

    assert set(schema["properties"]) == {"note"}
    assert schema["required"] == ["note"]


@pytest.mark.django_db
def test_a_call_without_it_is_served() -> None:
    out = _server().call_tool("echo", {"note": "hi"}, user=None).to_dict()

    assert out["structuredContent"] == {"items": [{"note": "hi", "request_is_caller_value": False}]}


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["request", "serializer"])
def test_a_callers_value_for_a_name_the_listing_leaves_out_never_reaches_the_selector(
    name: str,
) -> None:
    # Under the kernel's closed argument set a caller sending either name is
    out = _server().call_tool("echo", {"note": "hi", name: "spoof"}, user=None).to_dict()

    assert out["isError"] is True
