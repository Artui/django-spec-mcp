"""What each of a tool call's seed names is to a kernel tool.

``request`` and ``view`` are the kernel's ``CALL_SEEDS``, read off the call's
context, and ``serializer`` is an ordinary argument, since a kernel Validator
binds nothing of that name. So ``serializer`` is advertised and received; a
read named ``request`` or ``view`` is refused by the kernel when the spec is
built, and one named after a seed the server registers is refused at
registration, because no caller's value of that name reaches the service. A
top-level Validator field of such a name is not a defect: the kernel documents
it as the caller's, validated into ``data`` and never handed to the service
under that name, so the last test here holds that answer.
"""

from __future__ import annotations

from dataclasses import make_dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    CALL_SEEDS,
    DEFAULT_POOL_SEEDS,
    DataclassValidator,
    Parameter,
    Parameters,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend


def _echo(**pool: Any) -> dict[str, Any]:
    # The whole pool arrives (``user``, ``progress`` beside the arguments), so
    # each test reads back only the name it sent.
    return pool


def _echoed(out: Any, name: str) -> Any:
    assert out.get("isError") is not True, out
    return out["structuredContent"][name]


SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")

EDGE_NAMES = sorted(CALL_SEEDS | {"serializer"})


def _server(spec: ServiceSpec) -> MCPServer:
    # A registered seed beside the call's own, so ``tenant`` is asked as a seed's name.
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=None, pool_seeds=SEEDS
    )
    server.register_tool(spec, name="echo", description="Echo the arguments.")
    return server


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


def _reading(name: str) -> ServiceSpec:
    """A spec taking ``name`` as a read."""
    # Named outright: a ``**`` catch-all is never handed a call seed's name.
    namespace: dict[str, Any] = {}
    exec(f"def service(*, {name}): return {{{name!r}: {name}}}", namespace)
    return ServiceSpec(
        service=namespace["service"],
        permissions=[Unrestricted()],
        reads=Parameters([Parameter(name, "string", required=True)]),
    )


@pytest.mark.parametrize("name", sorted(CALL_SEEDS))
def test_a_read_named_after_a_call_seed_is_refused_when_the_spec_is_built(name: str) -> None:
    # The kernel's own refusal: no registrar of this package is reached.
    with pytest.raises(ImproperlyConfigured, match=rf"\['{name}'\]"):
        _reading(name)


def test_a_read_named_after_a_seed_the_server_registers_is_refused_at_registration() -> None:
    # The spec builds, since the kernel cannot know this server's seeds; the
    # registrar asks ``check_seeds`` with them and refuses it naming the tool.
    spec = _reading("tenant")
    with pytest.raises(ImproperlyConfigured, match=r"MCP tool 'echo': .*\['tenant'\]"):
        _server(spec)


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


@pytest.mark.django_db
def test_a_validator_field_named_after_a_seed_is_the_callers_and_the_keyword_is_the_seeds() -> None:
    # The kernel (0.18.0) seats a field named after a seed as the caller's,
    # rather than refusing it. It validates into
    # ``data`` and never reaches the service under that name, so the ``tenant``
    # keyword reads the seed's value while the caller's own is in ``data``.
    def service(*, data: Any, tenant: str) -> dict[str, Any]:
        return {"data": dict(data) if isinstance(data, dict) else vars(data), "tenant": tenant}

    spec = ServiceSpec(
        service=service,
        permissions=[Unrestricted()],
        validator=DataclassValidator(make_dataclass("_Input", [("tenant", str)])),
    )
    server = _server(spec)

    out = server.call_tool("echo", {"tenant": "globex"}, user=None).to_dict()

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"data": {"tenant": "globex"}, "tenant": "acme"}
