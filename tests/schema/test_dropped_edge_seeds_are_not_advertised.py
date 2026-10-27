"""A kernel read the call's context fills is not offered to the model.

The drf-services original held that the DRF edge's own seeds, ``request`` and
``serializer``, were never advertised, because the edge drops a caller's value
for either. A kernel spec has no DRF edge and no ``serializer`` seed: a name
it does not declare is not an argument at all, and the closed set refuses it.
What carries over is ``request`` (and ``view``): a read marked ``reflected``
under either name is filled from the call's context on any mount
(``server_owned_keys``), so a caller is never asked for it. The original is
kept beside this file as ``test_dropped_edge_seeds_are_not_advertised_drfs.py``.

Read through ``MCPServer.list_tools``, so each test reads what a client is served.
"""

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
                    # in its context, so there is nothing to fill it with.
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
    # so offering it would ask a caller for a value never used.
    schema = _schema(_server())

    assert set(schema["properties"]) == {"note"}
    assert schema["required"] == ["note"]


@pytest.mark.django_db
def test_a_call_without_it_is_served() -> None:
    out = _server().call_tool("echo", {"note": "hi"}, user=None).to_dict()

    assert out["structuredContent"] == [{"note": "hi", "request_is_caller_value": False}]


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["request", "serializer"])
def test_a_callers_value_for_a_name_the_listing_leaves_out_never_reaches_the_selector(
    name: str,
) -> None:
    # Changed by design: the DRF edge dropped a caller's ``request`` and
    # ``serializer`` silently. Under the kernel's closed argument set a caller
    # sending either is refused, so the spoofed value reaches nothing.
    out = _server().call_tool("echo", {"note": "hi", name: "spoof"}, user=None).to_dict()

    assert out["isError"] is True
