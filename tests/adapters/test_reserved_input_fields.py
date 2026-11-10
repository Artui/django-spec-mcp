from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from tests.utils import make_server

_SEEDED = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")


@dataclass
class _User:
    user: str


@dataclass
class _Tenant:
    tenant: str


def test_a_validator_field_named_after_a_reserved_seed_is_refused_at_registration() -> None:
    # The kernel refuses it when the spec's parameters are assembled, which is
    spec = ServiceSpec(
        service=lambda *, data: None,
        atomic=False,
        permissions=[Unrestricted()],
        validator=DataclassValidator(_User),
    )

    with pytest.raises(ImproperlyConfigured, match=r"declares the parameter\(s\) \['user'\]"):
        make_server().register_tool(spec, name="t")


@pytest.mark.parametrize("kind", ["service", "selector"])
def test_a_read_named_after_a_reserved_seed_is_refused_when_the_spec_is_built(kind: str) -> None:
    reads = Parameters([Parameter("instance", "string", required=True)])

    with pytest.raises(
        ImproperlyConfigured, match=r"reads declares the parameter\(s\) \['instance'\]"
    ):
        if kind == "service":
            ServiceSpec(service=lambda: None, atomic=False, reads=reads)
        else:
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None, reads=reads)


def test_a_validator_field_named_like_a_registered_seed_is_the_callers_and_reaches_data() -> None:
    # Refusing the field would be wrong: the seed would silently outrank it in
    server = make_server(pool_seeds=_SEEDED)
    server.register_tool(
        ServiceSpec(
            service=lambda *, data, tenant: {"sent": data.tenant, "seeded": tenant},
            atomic=False,
            permissions=[Unrestricted()],
            validator=DataclassValidator(_Tenant),
        ),
        name="t",
    )
    listed: Any = server.list_tools(user=None)

    out = server.call_tool("t", {"tenant": "mine"}, user=None).to_dict()

    assert listed["tools"][0]["inputSchema"]["required"] == ["tenant"]
    assert out["structuredContent"] == {"sent": "mine", "seeded": "acme"}


def test_a_read_named_like_a_registered_seed_is_refused_at_registration() -> None:
    # Built outside the block: the spec is valid on its own, and only a server
    spec = ServiceSpec(
        service=lambda *, tenant: tenant,
        atomic=False,
        permissions=[Unrestricted()],
        reads=Parameters([Parameter("tenant", "string", required=True)]),
    )
    server = make_server(pool_seeds=_SEEDED)
    with pytest.raises(ImproperlyConfigured, match=r"MCP tool 't': .*\['tenant'\]"):
        server.register_tool(spec, name="t")
    assert "t" not in server.tools


@pytest.mark.parametrize("name", ["request", "view"])
def test_a_read_named_after_the_calls_own_seat_is_refused_at_registration(name: str) -> None:
    # A selector spec's own read of either name is not refused when it is
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda **_: None,
        permissions=[Unrestricted()],
        reads=Parameters([Parameter(name, "string", required=True)]),
    )
    server = make_server()
    with pytest.raises(ImproperlyConfigured, match=rf"MCP tool 't': .*\['{name}'\]"):
        server.register_tool(spec, name="t")
    assert "t" not in server.tools
