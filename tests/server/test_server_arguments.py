"""``server_arguments=`` on a kernel tool: declared parameters the server answers for.

A kernel tool registered with a ``ServerArguments`` takes the bound names from
the server and the withheld ones from no one, and the caller is offered
neither: ``tools/list`` leaves them out of the ``inputSchema`` and its
``required``. A bound value wins over whatever the caller sends under its name,
a bound value fills a read the spec requires, and a binding that cannot mean
what it says is refused at registration naming the tool, however the binding
reached the registry. Whether a value reaches the callable through each
registrar is asked in ``test_server_arguments_on_every_registrar.py``.

The drf-services original is kept beside this file as
``test_server_arguments_drfs.py``. Its ``UrlKwarg`` route captures and
``QueryParam`` query values have no kernel counterpart, since a kernel spec's
reads are its arguments wherever they come from; nor does its logging of a
caller restating a bound value, which the drf-services dispatch wrote. Its wire
and in-process questions are asked here in process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding


@dataclass
class _Card:
    project: int
    draw: int


@dataclass
class _Note:
    project: int
    note: str


def _server(**kwargs: Any) -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **kwargs)


def _card(*, project: int, draw: int = 0) -> _Card:
    return _Card(project=project, draw=draw)


def _selector(*, project_required: bool = True) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_card,
        reads=Parameters(
            [
                Parameter("project", "integer", required=project_required),
                Parameter("draw", "integer", required=False),
            ]
        ),
        presenter=DataclassPresenter(_Card),
        permissions=[Unrestricted()],
    )


def _service() -> ServiceSpec:
    return ServiceSpec(
        service=lambda *, project, note: _Note(project=project, note=note),
        validator=DataclassValidator(_Note),
        presenter=DataclassPresenter(_Note),
        permissions=[Unrestricted()],
    )


def _schema(server: MCPServer, name: str) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return next(tool for tool in listed["tools"] if tool["name"] == name)["inputSchema"]


_SCOPED = ServerArguments(values={"project": 7}, withheld=frozenset({"draw"}))


def test_the_bound_and_withheld_names_leave_the_listing() -> None:
    server = _server()
    server.register_tool(_selector(), name="card", description="d", server_arguments=_SCOPED)
    schema = _schema(server, "card")
    assert "project" not in schema.get("properties", {})
    assert "draw" not in schema.get("properties", {})
    assert "project" not in schema.get("required", [])


def test_a_binding_fills_a_required_read() -> None:
    server = _server()
    server.register_tool(_selector(), name="card", description="d", server_arguments=_SCOPED)
    result = server.call_tool("card", {}, user=None)
    assert result.structured_content == {"project": 7, "draw": 0}


def test_the_bound_value_wins_and_the_withheld_one_is_dropped() -> None:
    server = _server()
    server.register_tool(_selector(), name="card", description="d", server_arguments=_SCOPED)
    result = server.call_tool("card", {"project": 99, "draw": 3}, user=None)
    # Both are dropped rather than refused, as the original's bound project
    # wins and its withheld sibling is dropped.
    assert result.is_error is False
    assert result.structured_content == {"project": 7, "draw": 0}


@pytest.mark.django_db
def test_a_service_tools_schema_subtracts_the_bound_field() -> None:
    server = _server()
    bind = ServerArguments(values={"project": 7})
    server.register_tool(_service(), name="note", description="d", server_arguments=bind)
    schema = _schema(server, "note")
    assert set(schema["properties"]) == {"note"}
    result = server.call_tool("note", {"note": "hi"}, user=None)
    assert result.structured_content == {"project": 7, "note": "hi"}


def test_withholding_a_name_no_call_can_do_without_is_refused() -> None:
    server = _server()
    bind = ServerArguments(withheld=frozenset({"project"}))
    with pytest.raises(ImproperlyConfigured, match="'card'"):
        server.register_tool(_selector(), name="card", description="d", server_arguments=bind)
    assert "card" not in server.tools


def test_a_binding_built_by_hand_is_checked_when_it_is_registered() -> None:
    server = _server()
    binding = KernelToolBinding(
        name="card",
        description="d",
        spec=_selector(),
        server_arguments=ServerArguments(values={"tenant": 1}),
    )
    with pytest.raises(ImproperlyConfigured, match=r"MCP tool 'card'.*tenant"):
        server.tools.register(binding)
    assert "card" not in server.tools


def test_a_registered_seed_is_refused_with_the_servers_own_seeds() -> None:
    """The check reads the seeds this server dispatches with, not the defaults."""
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, tenant: _Card(project=1, draw=0),
        reads=Parameters([Parameter("tenant", "string", required=True, reflected=True)]),
        presenter=DataclassPresenter(_Card),
        permissions=[Unrestricted()],
    )
    bind = ServerArguments(values={"tenant": "acme"})
    _server().register_tool(spec, name="plain", description="d", server_arguments=bind)
    seeded = _server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
    with pytest.raises(ImproperlyConfigured, match="'plain'"):
        seeded.register_tool(spec, name="plain", description="d", server_arguments=bind)


def test_no_binding_leaves_the_tool_as_it_was() -> None:
    server = _server()
    server.register_tool(_selector(project_required=False), name="card", description="d")
    assert set(_schema(server, "card")["properties"]) == {"project", "draw"}
    result = server.call_tool("card", {"project": 3}, user=None)
    assert result.structured_content == {"project": 3, "draw": 0}
