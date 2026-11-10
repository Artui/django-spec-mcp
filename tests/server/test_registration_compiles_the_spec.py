from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

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

from tests.utils import make_server


@dataclass
class _Row:
    pk: int


def _rows() -> list[_Row]:
    return [_Row(pk=1)]


def _spec(read: str) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        reads=Parameters([Parameter(read, "string", required=False)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )


@pytest.mark.parametrize("read", ["request", "view", "tenant"])
def test_a_read_a_seed_occupies_is_refused_at_registration(read: str) -> None:
    server = make_server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
    with pytest.raises(ImproperlyConfigured, match=rf"'sel'.*\['{read}'\]"):
        server.register_tool(_spec(read), name="sel", description="d", paginate=True)
    assert "sel" not in server.tools


def test_a_read_named_otherwise_registers_and_is_called() -> None:
    server = make_server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
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
    server = make_server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
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
