"""A kernel tool's argument named after a value dispatch fills itself.

Asked of kernel specs registered with ``register_tool``, where a seed and an
argument of one name meet in three ways:

- a parameter named after one of the kernel's ``RESERVED_POOL_SEEDS`` (``user``,
  ``data``, ``instance`` and the rest) is refused by the kernel when the spec's
  parameters are assembled, so no tool can carry it;
- a top-level Validator field named like a seed the server registers stays the
  caller's: it is advertised, validates into ``data``, and the service's
  parameter of that name keeps the seed;
- a ``reads`` entry nobody marked ``reflected``, named like a registered seed or
  like the call's own ``request`` / ``view``, is refused by the kernel's
  ``dispatch`` on every call. ``register_tool`` registers it, so the tool is
  listed and no call to it can succeed. Those two tests are strict xfails until
  registration refuses it, which needs the kernel to export the reader
  ``dispatch`` refuses with and ``MCPServer`` to hand its ``pool_seeds`` to
  ``kernel_spec_to_tool``.

The drf-services original asked this of an ``input_serializer`` field under each
``ArgumentBinding``, refused at registration because the seed silently won the
service's pool; on the kernel the Validator's field reaches ``data`` instead, so
the refusal is no longer the answer for it. It is kept beside this file as
``test_reserved_input_fields_drfs.py``.
"""

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

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_SEEDED = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")


def _server(**options: Any) -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **options)


@dataclass
class _User:
    user: str


@dataclass
class _Tenant:
    tenant: str


def test_a_validator_field_named_after_a_reserved_seed_is_refused_at_registration() -> None:
    # The kernel refuses it when the spec's parameters are assembled, which is
    # at construction or, where the Validator's annotations are strings, at the
    # first ``parameters()``; ``register_tool`` asks for them to compile the
    # schema, so either way no listing ever shows the field.
    spec = ServiceSpec(
        service=lambda *, data: None,
        atomic=False,
        permissions=[Unrestricted()],
        validator=DataclassValidator(_User),
    )

    with pytest.raises(ImproperlyConfigured, match=r"declares the parameter\(s\) \['user'\]"):
        _server().register_tool(spec, name="t")


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
    # Changed by design: the drf-services path refused such a field, because
    # the seed silently outranked it in the service's pool. The kernel keeps
    # the caller's value in ``data`` and the seed in the parameter, so each
    # reaches the service under its own name and nothing is lost.
    server = _server(pool_seeds=_SEEDED)
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


@pytest.mark.xfail(
    strict=True,
    # "DID NOT RAISE" only, so no other error can keep the test quietly xfailed.
    raises=pytest.fail.Exception,
    reason=(
        "register_tool accepts a read a registered seed occupies, which dispatch refuses "
        "on every call. Fix: the kernel exports the reader dispatch refuses with "
        "(dispatch.utils.seed_named_parameters), kernel_spec_to_tool refuses what it "
        "names, and MCPServer._kernel_tool_binding passes pool_seeds=self._pool_seeds."
    ),
)
def test_a_read_named_like_a_registered_seed_is_refused_at_registration() -> None:
    # The spec is built inside the block, so a refusal from either layer, the
    # spec's construction or the registration, turns the xfail into an XPASS.
    with pytest.raises(ImproperlyConfigured, match="tenant"):
        spec = ServiceSpec(
            service=lambda *, tenant: tenant,
            atomic=False,
            permissions=[Unrestricted()],
            reads=Parameters([Parameter("tenant", "string", required=True)]),
        )
        _server(pool_seeds=_SEEDED).register_tool(spec, name="t")


@pytest.mark.xfail(
    strict=True,
    # "DID NOT RAISE" only, so no other error can keep the test quietly xfailed.
    raises=pytest.fail.Exception,
    reason=(
        "register_tool accepts a read named request or view, which dispatch refuses on "
        "every mount and every call. Fix: the kernel refuses a read of either name nobody "
        "marked reflected when the spec is built, as it refuses RESERVED_POOL_SEEDS, or "
        "exports the reader dispatch refuses with for kernel_spec_to_tool to ask."
    ),
)
@pytest.mark.parametrize("name", ["request", "view"])
def test_a_read_named_after_the_calls_own_seat_is_refused_at_registration(name: str) -> None:
    with pytest.raises(ImproperlyConfigured, match=name):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda **_: None,
            permissions=[Unrestricted()],
            reads=Parameters([Parameter(name, "string", required=True)]),
        )
        _server().register_tool(spec, name="t")
