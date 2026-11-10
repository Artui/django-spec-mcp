from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceNotFound,
    ServiceSpec,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    admit_kernel_tool_call,
    tool_call_context,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import MODERN, make_server, post_in_era, tool_error


class _Tenant:
    """A seed's value: a fresh object per resolve, so two resolves are two objects."""


class _Witness:
    """The seed's resolver, and what the check and the run were each handed."""

    def __init__(self) -> None:
        self.resolved: list[_Tenant] = []
        self.checked: list[Any] = []
        self.ran: list[Any] = []

    def resolve(self) -> _Tenant:
        tenant = _Tenant()
        self.resolved.append(tenant)
        return tenant

    def assert_one_value(self) -> None:
        # Once per call, and the one object both sides saw.
        assert len(self.resolved) == 1, self.resolved
        assert len(self.checked) == 1, self.checked
        assert len(self.ran) == 1, self.ran
        assert self.checked[0] is self.resolved[0]
        assert self.ran[0] is self.resolved[0]


class _NamesTenant(PermissionCheck):
    """Admits every caller, recording the ``tenant`` seed it names."""

    def __init__(self, witness: _Witness) -> None:
        self.witness = witness

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        self.witness.checked.append(tenant)
        return True


def _server(witness: _Witness) -> MCPServer:
    server = make_server(
        session_store=InMemorySessionStore(),
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=witness.resolve),
    )

    def whoami(*, tenant: Any) -> dict[str, str]:
        witness.ran.append(tenant)
        return {"tenant": "seen"}

    def card(*, slug: str, tenant: Any) -> dict[str, str]:
        witness.ran.append(tenant)
        return {"slug": slug}

    server.register_tool(
        ServiceSpec(service=whoami, permissions=[_NamesTenant(witness)]),
        name="tenant.whoami",
        description="d",
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=card,
            reads=Parameters(
                [
                    Parameter("slug", "string", required=True),
                    Parameter("tenant", "string", required=True, reflected=True),
                ]
            ),
            permissions=[_NamesTenant(witness)],
        ),
        name="tenant.card",
        uri_template="tenants://{slug}",
    )
    return server


def _call_over_the_wire(witness: _Witness, *, is_async: bool) -> Any:
    with override_settings(ROOT_URLCONF=urlconf_for(_server(witness), is_async=is_async)):
        return post_in_era(
            MODERN, is_async, "tools/call", {"name": "tenant.whoami", "arguments": {}}
        )


def _read_over_the_wire(witness: _Witness, *, is_async: bool) -> Any:
    with override_settings(ROOT_URLCONF=urlconf_for(_server(witness), is_async=is_async)):
        return post_in_era(MODERN, is_async, "resources/read", {"uri": "tenants://north"})


# ---------- the five dispatch sites ----------


@pytest.mark.django_db(transaction=True)
def test_a_wire_tool_call_resolves_a_checks_seed_once() -> None:
    witness = _Witness()

    result = _call_over_the_wire(witness, is_async=False)

    assert result["structuredContent"] == {"tenant": "seen"}, result
    witness.assert_one_value()


@pytest.mark.django_db(transaction=True)
def test_an_async_wire_tool_call_resolves_a_checks_seed_once() -> None:
    witness = _Witness()

    result = _call_over_the_wire(witness, is_async=True)

    assert result["structuredContent"] == {"tenant": "seen"}, result
    witness.assert_one_value()


@pytest.mark.django_db(transaction=True)
def test_a_resource_read_resolves_a_checks_seed_once() -> None:
    witness = _Witness()

    result = _read_over_the_wire(witness, is_async=False)

    assert json.loads(result["contents"][0]["text"]) == {"slug": "north"}, result
    witness.assert_one_value()


@pytest.mark.django_db(transaction=True)
def test_an_async_resource_read_resolves_a_checks_seed_once() -> None:
    witness = _Witness()

    result = _read_over_the_wire(witness, is_async=True)

    assert json.loads(result["contents"][0]["text"]) == {"slug": "north"}, result
    witness.assert_one_value()


@pytest.mark.django_db
def test_an_in_process_call_tool_resolves_a_checks_seed_once() -> None:
    witness = _Witness()

    result = _server(witness).call_tool("tenant.whoami", {}, user=None).to_dict()

    assert result["structuredContent"] == {"tenant": "seen"}, result
    witness.assert_one_value()


# ---------- the fallbacks carry no carrier ----------


@dataclass
class _Line:
    number: str
    amount_cents: int


def _fallback_server(witness: _Witness, check: PermissionCheck) -> MCPServer:
    server = make_server(
        session_store=None, pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=witness.resolve)
    )

    def take(*, pk: int, tenant: Any) -> dict[str, int]:
        witness.ran.append(tenant)
        return {"pk": pk}

    def take_lines(*, data: list[_Line], tenant: Any) -> list[_Line]:
        witness.ran.append(tenant)
        return data

    server.register_tool(
        ServiceSpec(
            service=take,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=[check],
        ),
        name="get",
        description="d",
    )
    server.register_tool(
        ServiceSpec(
            service=take_lines,
            many=True,
            validator=DataclassValidator(_Line),
            permissions=[check],
        ),
        name="bulk",
        description="d",
    )
    return server


def _context(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None, scopes=()),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=MODERN,
        client_capabilities={"extensions": {}},
        tasks=None,
        task_executor=None,
        config=server.config,
        pool_seeds=server.pool_seeds,
        conventions=server.conventions,
    )


def _admit(server: MCPServer, name: str, arguments: dict[str, Any]) -> KernelAdmission:
    binding = server.tools.get(name)
    context = _context(server)
    admission = admit_kernel_tool_call(
        binding, arguments, context, tool_call_context(binding, context)
    )
    assert isinstance(admission, KernelAdmission), admission
    return admission


class _RaisesNotFound(PermissionCheck):
    """Names the seed, then raises a refusal that is no denial."""

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        raise ServiceNotFound("No such tenant.")


@pytest.mark.django_db
def test_an_admitted_call_carries_the_seeds_its_checks_were_offered() -> None:
    witness = _Witness()
    server = _fallback_server(witness, _NamesTenant(witness))

    admission = _admit(server, "get", {"pk": 1})

    assert admission.seeds is not None
    assert admission.seeds.values["tenant"] is witness.checked[0]
    assert admission.seeds.covers(
        server.tools.get("get").spec,
        admission.principal,
        admission.call_context,
        server.pool_seeds,
    )


@pytest.mark.django_db
def test_a_call_failing_its_shape_carries_no_seeds_and_is_answered_as_before() -> None:
    witness = _Witness()
    server = _fallback_server(witness, _NamesTenant(witness))

    assert _admit(server, "get", {}).seeds is None
    witness.resolved.clear()

    out = handle_tools_call({"name": "get", "arguments": {}}, _context(server))
    assert tool_error(out)["detail"] == {"pk": ["This field is required."]}, out
    # Resolved once, for the judgment on no arguments; dispatch refuses the
    assert len(witness.resolved) == 1, witness.resolved


@pytest.mark.django_db
def test_a_many_call_with_a_refused_row_carries_no_seeds_and_is_answered_as_before() -> None:
    witness = _Witness()
    server = _fallback_server(witness, _NamesTenant(witness))
    rows = {"items": [{"number": "A-1", "amount_cents": "x"}]}

    assert _admit(server, "bulk", rows).seeds is None
    witness.resolved.clear()

    out = handle_tools_call({"name": "bulk", "arguments": rows}, _context(server))
    assert tool_error(out)["detail"] == {
        "items": {"0": {"amount_cents": ["Expected an integer."]}}
    }, out
    # Resolved by the prelude and again by dispatch, which asks the checks
    assert len(witness.resolved) == 2, witness.resolved


@pytest.mark.django_db
def test_a_refusal_that_is_no_denial_carries_no_seeds_and_is_answered_as_before() -> None:
    witness = _Witness()
    server = _fallback_server(witness, _RaisesNotFound())

    assert _admit(server, "get", {"pk": 1}).seeds is None
    witness.resolved.clear()

    out = handle_tools_call({"name": "get", "arguments": {"pk": 1}}, _context(server))
    error = tool_error(out)
    assert error["type"] == "service_error", error
    assert "No such tenant." in str(error)
    # Resolved by the prelude and again by dispatch, which raises the refusal again.
    assert len(witness.resolved) == 2, witness.resolved


@pytest.mark.django_db(transaction=True)
async def test_an_in_process_acall_tool_resolves_a_checks_seed_once() -> None:
    # ``acall_tool`` admits off the loop and dispatches through
    witness = _Witness()

    out = await _server(witness).acall_tool("tenant.whoami", {}, user=None)

    assert out["structuredContent"] == {"tenant": "seen"}, out
    witness.assert_one_value()
