"""``server_arguments=`` on a kernel tool: declared parameters the server answers for.

A kernel tool registered with a ``ServerArguments`` takes the bound names from
the server and the withheld ones from no one, and the caller is offered
neither: ``tools/list`` leaves them out of the ``inputSchema`` and its
``required``. A bound value wins over whatever the caller sends under its name,
a bound value fills a read the spec requires, and a binding that cannot mean
what it says is refused at registration naming the tool, however the binding
reached the registry. A caller restating a bound value with a different one is
logged once, by tool and argument name and never by value, and a retry's
``inputResponses`` cannot replace a bound value, on the synchronous and the
asynchronous handler alike. Whether a value reaches the callable through each
registrar is asked in ``test_server_arguments_on_every_registrar.py``.

The drf-services original is kept beside this file as
``test_server_arguments_drfs.py``. Its ``UrlKwarg`` route captures and
``QueryParam`` query values have no kernel counterpart, since a kernel spec's
reads are its arguments wherever they come from. Its logging of a caller
restating a bound value is shared: the kernel route calls the same
``apply_tool_server_arguments``, so it is asked here of a kernel tool. Its wire
and in-process questions are asked here in process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
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
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from tests.subscriptions.test_subscription_core import _context


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


def _selector() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_card,
        reads=Parameters(
            [
                Parameter("project", "integer", required=True),
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


def test_the_bound_value_wins_and_the_withheld_one_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = _server()
    server.register_tool(_selector(), name="card", description="d", server_arguments=_SCOPED)
    with caplog.at_level(logging.WARNING, logger="django_spec_mcp"):
        result = server.call_tool("card", {"project": 99, "draw": 3}, user=None)
    # Both are dropped rather than refused, as the original's bound project
    # wins and its withheld sibling is dropped.
    assert result.is_error is False
    assert result.structured_content == {"project": 7, "draw": 0}
    # Held by the withheld conjunct of ``apply_tool_server_arguments``: a
    # withheld name is logged whenever the caller sent it.
    (message,) = _dropped(caplog)
    assert "draw" in message
    assert "project" in message


def _optional_reads(selector: Any, *names: str) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=selector,
        reads=Parameters([Parameter(name, "integer") for name in names]),
        presenter=DataclassPresenter(_Card),
        permissions=[Unrestricted()],
    )


def test_an_optional_read_the_server_binds_registers_and_is_filled() -> None:
    # An optional read over a keyword with no default is refused at
    # registration, since a call may leave it out; a bound one never is left
    # out, because the server's value replaces the caller's on every call.
    server = _server()
    bind = ServerArguments(values={"project": 7})
    server.register_tool(
        _optional_reads(_card, "project", "draw"),
        name="card",
        description="d",
        server_arguments=bind,
    )
    result = server.call_tool("card", {}, user=None)
    assert result.structured_content == {"project": 7, "draw": 0}


def _card_for(*, project: int, owner: int) -> _Card:
    return _Card(project=project, draw=owner)


def test_an_optional_read_the_server_does_not_bind_is_still_refused() -> None:
    server = _server()
    bind = ServerArguments(values={"project": 7})
    with pytest.raises(
        ImproperlyConfigured, match=r"MCP tool 'card': .*\['owner'\], declared as optional"
    ):
        server.register_tool(
            _optional_reads(_card_for, "project", "owner"),
            name="card",
            description="d",
            server_arguments=bind,
        )
    assert "card" not in server.tools


def _renamed(*, instance: Any) -> _Card:
    return instance


@pytest.mark.parametrize("seat", ["instance_selector_spec", "collection_selector_spec"])
def test_an_optional_lookup_read_the_server_binds_registers_and_is_filled(seat: str) -> None:
    # The binding reaches the target selector's reads too, which are the
    # call's arguments as the spec's own are.
    lookup = SelectorSpec(
        kind=SelectorKind.RETRIEVE if seat == "instance_selector_spec" else SelectorKind.LIST,
        selector=lambda *, project: _Card(project=project, draw=0),
        reads=Parameters([Parameter("project", "integer")]),
    )
    target = "instance" if seat == "instance_selector_spec" else "collection"
    spec = ServiceSpec(
        service=lambda **pool: pool[target],
        permissions=[Unrestricted()],
        atomic=False,
        **{seat: lookup},
    )
    server = _server()
    bind = ServerArguments(values={"project": 7})
    server.register_tool(spec, name="act", description="d", server_arguments=bind)
    result = server.call_tool("act", {}, user=None)
    assert result.is_error is False, result.to_dict()


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
    server.register_tool(_selector(), name="card", description="d")
    assert set(_schema(server, "card")["properties"]) == {"project", "draw"}
    result = server.call_tool("card", {"project": 3}, user=None)
    assert result.structured_content == {"project": 3, "draw": 0}


def _dropped(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if "dropped the caller's value" in r.getMessage()
    ]


def test_a_caller_restating_a_bound_value_with_another_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Held by the ``repr(...) != repr(...)`` conjunct of ``apply_tool_server_arguments``.

    The restated call below logs nothing only because the comparison sees the
    caller's value equal the server's; without it both calls would log, which is
    also what a task worker replaying already-applied arguments would do.
    """
    server = _server()
    bind = ServerArguments(values={"project": 7})
    server.register_tool(_selector(), name="rows", description="d", server_arguments=bind)

    with caplog.at_level(logging.WARNING, logger="django_spec_mcp"):
        server.call_tool("rows", {"project": 9}, user=None)
        server.call_tool("rows", {"project": 7}, user=None)

    (message,) = _dropped(caplog)
    assert "MCP tool 'rows'" in message
    assert "project" in message
    # The values are the consumer's data: only the names are logged.
    assert "9" not in message


@pytest.mark.django_db
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_an_answer_cannot_replace_a_bound_value(is_async: bool) -> None:
    """A retry's ``inputResponses`` merge over the arguments with no limit on their keys.

    A first call can carry one too. Held by the second
    ``apply_tool_server_arguments`` in ``handle_tools_call`` and its async
    sibling, applied over what the answers produced.
    """
    server = _server()
    bind = ServerArguments(values={"project": 7})
    server.register_tool(_service(), name="note", description="d", server_arguments=bind)
    params = {
        "name": "note",
        "arguments": {"note": "hi"},
        "inputResponses": {ELICITATION_KEY: {"action": "accept", "content": {"project": 8}}},
    }
    context = _context(server, client_capabilities={"elicitation": {}})

    if is_async:
        result = async_to_sync(handle_tools_call_async)(params, context)
    else:
        result = handle_tools_call(params, context)

    assert not result.get("isError"), result
    assert result["structuredContent"] == {"project": 7, "note": "hi"}
