"""A kernel spec this server refuses on every listing or call is refused when registered.

A presenter whose markings cannot be projected, two fields marked as the
record's label say, is refused by the kernel as the projection is derived, and
the listing derives it for every tool: such a tool registered, and then
``tools/list`` raised for every tool on the server and each call failed.
Registration derives the projection, so the refusal names the tool at startup.

A kernel spec is checked as it is constructed, so most of what drf-services
refused only when it compiled a spec on first read (an output selector
declaring ``preconditions``, a ``reads`` entry the dispatcher seeds, such as
``user``) never reaches a registrar. One refusal still arrives late: a read
named after a seed this server's calls carry, the call's own ``request`` and
``view`` or a seed registered on ``pool_seeds=``. The kernel refuses it at
dispatch, so the tool registers and fails every call with
``ImproperlyConfigured``; it is listed with the read as an argument for
``request`` and ``view``, but a read named after a registered seed is absent
from the ``inputSchema``, since a seed answers it. Registration should refuse
it.

The kernel decides which reads a seed may share a name with (a ``reflected``
read, a top-level Validator field) in ``dispatch.utils.seed_named_parameters``,
which it does not export, and every local copy of such a reader has drifted
from dispatch, so the refusal waits on that export and the tests are strict
xfails until then.

The drf-services original is kept beside this file as
``test_registration_compiles_the_spec_drfs.py``; its ``QueryParam`` and
``field_audiences`` cases have no kernel counterpart, because a kernel spec
declares reads with ``Parameter`` and carries its markings on its presenter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    FieldMarking,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_AWAITS_THE_KERNEL = (
    "the kernel's seed_named_parameters is not exported; registration calls it "
    "for every kernel tool once it is"
)


@dataclass
class _Row:
    pk: int


def _rows() -> list[_Row]:
    return [_Row(pk=1)]


def _server() -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )


def _spec(read: str) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        reads=Parameters([Parameter(read, "string", required=False)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )


@pytest.mark.xfail(strict=True, reason=_AWAITS_THE_KERNEL)
@pytest.mark.parametrize("read", ["request", "view", "tenant"])
def test_a_read_a_seed_occupies_is_refused_at_registration(read: str) -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=rf"'sel'.*\['{read}'\]"):
        server.register_tool(_spec(read), name="sel", description="d", paginate=True)
    assert "sel" not in server.tools


def test_a_read_named_otherwise_registers_and_is_called() -> None:
    server = _server()
    server.register_tool(_spec("serializer"), name="sel", description="d", paginate=True)
    result = server.call_tool("sel", {"serializer": "x"}, user=None)
    assert result.is_error is False


@dataclass
class _TwoLabels:
    pk: Annotated[int, FieldMarking.label()]
    number: Annotated[str, FieldMarking.label()]


def _two_labels() -> list[_TwoLabels]:
    return []


def test_a_presenter_whose_markings_cannot_project_is_refused_at_registration() -> None:
    server = _server()
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_two_labels,
        presenter=DataclassPresenter(_TwoLabels),
        permissions=[Unrestricted()],
    )
    with pytest.raises(ImproperlyConfigured, match=r"MCP tool 'two': .*A record has one name"):
        server.register_tool(spec, name="two", description="d", paginate=True)
    assert "two" not in server.tools
    # The rest of the server still lists.
    server.register_tool(_spec("status"), name="other", description="d", paginate=True)
    assert [tool["name"] for tool in server.list_tools(user=None)["tools"]] == ["other"]


@pytest.mark.parametrize(("read", "listed"), [("request", True), ("view", True), ("tenant", False)])
def test_a_read_a_seed_occupies_is_listed_only_for_request_and_view(
    read: str, listed: bool
) -> None:
    """The module docstring's claim about the ``inputSchema`` of each late-refused read."""
    server = _server()
    server.register_tool(_spec(read), name="sel", description="d", paginate=True)
    tools: Any = server.list_tools(user=None)["tools"]
    (schema,) = (tool["inputSchema"] for tool in tools)
    assert (read in schema["properties"]) is listed
