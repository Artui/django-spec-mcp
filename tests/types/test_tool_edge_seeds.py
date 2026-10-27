"""What each name in ``TOOL_EDGE_SEEDS`` is to a kernel tool call.

The constant is the DRF edge's: drf-services seeds ``request`` and ``view`` on
every ``dispatch_spec`` and its validator binds ``serializer``, so the drfs
registrars reserve the three beside the kernel's ``RESERVED_POOL_SEEDS``. On the
kernel path the first two are the kernel's own ``CALL_SEEDS``, read off the
call's context, and ``serializer`` is an ordinary argument, since a kernel
Validator binds nothing of that name. So this asks what each of the three is to
a kernel tool: ``serializer`` is advertised and received; a read or a top-level
Validator field named ``request`` or ``view``, or after a seed the server
registers, should be refused at registration, because no caller's value of that
name reaches the service. Both register and are listed today, which is the
defect the strict xfail below holds.

The drf-services original pinned the constant to drf-services' own
``EDGE_SEEDS`` and ``DROPPED_EDGE_SEEDS``; it is kept beside this file as
``test_tool_edge_seeds_drfs.py``. The constant goes with the drfs registrars.
"""

from __future__ import annotations

from dataclasses import make_dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    CALL_SEEDS,
    DEFAULT_POOL_SEEDS,
    RESERVED_POOL_SEEDS,
    DataclassValidator,
    Parameter,
    Parameters,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.types.tool_edge_seeds import TOOL_EDGE_SEEDS


def _echo(**pool: Any) -> dict[str, Any]:
    # The whole pool arrives (``user``, ``progress`` beside the arguments), so
    # each test reads back only the name it sent.
    return pool


def _echoed(out: Any, name: str) -> Any:
    assert out.get("isError") is not True, out
    return out["structuredContent"][name]


SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")

EDGE_NAMES = sorted(CALL_SEEDS | {"serializer"})
"""The constant's names, spelled out: the constant goes with the drfs registrars,
and these questions outlive it."""


def _server(spec: ServiceSpec) -> MCPServer:
    # A registered seed beside the call's own, so ``tenant`` is asked as a seed's name.
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=None, pool_seeds=SEEDS
    )
    server.register_tool(spec, name="echo", description="Echo the arguments.")
    return server


def test_the_tool_edge_seeds_are_the_kernels_call_seeds_and_the_drf_validators_name() -> None:
    assert CALL_SEEDS < TOOL_EDGE_SEEDS
    assert {"serializer"} == TOOL_EDGE_SEEDS - CALL_SEEDS
    assert not (TOOL_EDGE_SEEDS & RESERVED_POOL_SEEDS)


@pytest.mark.django_db
def test_a_kernel_read_named_serializer_is_advertised_and_received() -> None:
    name = "serializer"
    spec = ServiceSpec(
        service=_echo,
        permissions=[Unrestricted()],
        reads=Parameters([Parameter(name, "string", required=True)]),
    )
    server = _server(spec)
    listed: Any = server.list_tools(user=None)

    (tool,) = listed["tools"]
    out = server.call_tool("echo", {name: "the caller's"}, user=None).to_dict()

    assert tool["inputSchema"]["properties"] == {name: {"type": "string"}}
    assert _echoed(out, name) == "the caller's"


def _declaring(name: str, declaration: str) -> ServiceSpec:
    """A spec taking ``name`` as a read or as a top-level Validator field."""
    # Named outright: a ``**`` catch-all is never handed a call seed's name.
    namespace: dict[str, Any] = {}
    exec(f"def service(*, {name}): return {{{name!r}: {name}}}", namespace)
    if declaration == "read":
        declared: dict[str, Any] = {"reads": Parameters([Parameter(name, "string", required=True)])}
    else:
        declared = {"validator": DataclassValidator(make_dataclass("_Input", [(name, str)]))}
    return ServiceSpec(service=namespace["service"], permissions=[Unrestricted()], **declared)


@pytest.mark.xfail(
    strict=True,
    # Only "did not raise" counts, so a spec that failed to build is not taken
    # for the defect.
    raises=pytest.fail.Exception,
    reason=(
        "register_tool admits a declaration no caller's value can reach: a read named "
        "after a CALL_SEEDS name or a registered seed raises ImproperlyConfigured out of "
        "every dispatch, and a top-level Validator field of that name is advertised while "
        "the service is handed the call's request or view (None in process) or the seed's "
        "value. Fix: refuse both at registration, as the drfs registrars refuse a "
        "TOOL_EDGE_SEEDS or pool_seeds name, or have the kernel refuse or honour them."
    ),
)
@pytest.mark.parametrize("declaration", ["read", "validator field"])
@pytest.mark.parametrize("name", [*sorted(CALL_SEEDS), "tenant"])
def test_a_declaration_named_after_a_seed_is_refused_at_registration(
    name: str, declaration: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match=rf"\['{name}'\]"):
        _server(_declaring(name, declaration))


@pytest.mark.parametrize("name", ["call_context", "user"])
def test_a_kernel_read_named_after_a_reserved_seed_is_refused_by_the_kernel(name: str) -> None:
    # The kernel's own refusal, raised when the spec is built, before any
    # registrar of this package sees it.
    with pytest.raises(ImproperlyConfigured, match=rf"\['{name}'\], which dispatch seeds itself"):
        ServiceSpec(
            service=_echo,
            permissions=[Unrestricted()],
            reads=Parameters([Parameter(name, "string", required=True)]),
        )
