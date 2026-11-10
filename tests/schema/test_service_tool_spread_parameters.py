from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    ServiceSpec,
    UnknownArguments,
    Unrestricted,
    UnsetType,
)
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.kernel_tool_input_schema import kernel_tool_input_schema
from tests.utils import make_server


@dataclass
class _CloseTicket:
    ticket: int
    resolution: str
    tenant: int
    note: str = ""


def _close_ticket(*, ticket: int, resolution: str, tenant: int, note: str = "") -> None:
    return None


def _schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def _register(server: MCPServer, **declared: Any) -> None:
    server.register_tool(
        ServiceSpec(
            service=_close_ticket,
            validator=DataclassValidator(_CloseTicket),
            atomic=False,
            permissions=[Unrestricted()],
            **declared,
        ),
        name="close",
        description="Close a ticket.",
    )


def test_a_service_lists_its_validators_fields_at_the_top_level() -> None:
    # Not bundled under ``data``. A field without a default is required,
    server = make_server()
    _register(server)

    schema = _schema(server)

    assert set(schema["properties"]) == {"ticket", "resolution", "tenant", "note"}
    assert set(schema["required"]) == {"ticket", "resolution", "tenant"}
    assert schema["additionalProperties"] is False


def test_a_field_named_like_a_registered_seed_is_still_the_callers() -> None:
    # A registered seed's name is not subtracted from a spread service's parameters. A
    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: 1)
    server = make_server()
    _register(
        server,
        reads=Parameters([Parameter("region", "string", required=True, reflected=True)]),
    )
    binding = server.tools.get("close")
    assert isinstance(binding, KernelToolBinding)

    schema = kernel_tool_input_schema(
        binding,
        max_page_size=None,
        pool_seeds=seeds.extend(region=lambda: "eu"),
    )

    assert "tenant" in schema["properties"]
    assert "tenant" in schema["required"]
    assert "region" not in schema["properties"]


class _TenantScope(TypedDict):
    tenant: int


class _MaybeTenantScope(TypedDict):
    tenant: int | UnsetType


def _typed_input_data() -> _TenantScope:
    return {"tenant": 1}


def _declining_input_data() -> _MaybeTenantScope:
    return {"tenant": 1}


@pytest.mark.parametrize(
    ("provider", "properties", "required"),
    [
        # Merged over the caller's arguments before the Validator runs, so a
        pytest.param(
            _typed_input_data,
            {"ticket", "resolution", "note"},
            {"ticket", "resolution"},
            id="typed",
        ),
        pytest.param(
            _declining_input_data,
            {"ticket", "resolution", "tenant", "note"},
            {"ticket", "resolution"},
            id="declinable",
        ),
        pytest.param(
            lambda: {"tenant": 1},
            {"ticket", "resolution", "tenant", "note"},
            set(),
            id="untyped",
        ),
    ],
)
def test_a_field_the_input_data_provider_fills_is_offered_where_the_callers_value_is_served(
    provider: Any, properties: set[str], required: set[str]
) -> None:
    server = make_server()
    _register(server, input_data=provider)

    schema = _schema(server)

    assert set(schema["properties"]) == properties
    assert set(schema.get("required", [])) == required


def _close_by_tenant(*, tenant: int, ticket: int) -> None:
    return None


def test_a_service_without_a_validator_relaxes_its_reads_alone() -> None:
    # A service may declare its arguments as ``reads`` and no Validator; a
    server = make_server()
    server.register_tool(
        ServiceSpec(
            service=_close_by_tenant,
            reads=Parameters(
                [
                    Parameter("tenant", "integer", required=True),
                    Parameter("ticket", "integer", required=True),
                ]
            ),
            kwargs=lambda: {"tenant": 1},
            atomic=False,
            permissions=[Unrestricted()],
        ),
        name="close",
        description="Close a ticket.",
    )

    schema = _schema(server)

    assert set(schema["properties"]) == {"tenant", "ticket"}
    assert "required" not in schema


# ---------- what the call serves, on every route ----------


@dataclass
class _Archive:
    reason: str


class _Reason(TypedDict):
    reason: str


def _reason_of_record() -> _Reason:
    return {"reason": "provider"}


@dataclass
class _Archived:
    reason: str


def _archive_with_reason(*, reason: str) -> _Archived:
    return _Archived(reason=reason)


async def _served(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``archive`` through ``route`` returns as structured content."""
    if route == "call_tool":
        out: Any = (
            await sync_to_async(server.call_tool)("archive", arguments, user=None)
        ).to_dict()
    elif route == "acall_tool":
        out = await server.acall_tool("archive", arguments, user=None)
    else:
        context = MCPCallContext(
            http_request=HttpRequest(),
            token=TokenInfo(user=None),
            tools=server.tools,
            resources=server.resources,
            prompts=server.prompts,
            protocol_version="2025-11-25",
        )
        params = {"name": "archive", "arguments": arguments}
        out = await (
            handle_tools_call_async(params, context)
            if route == "async_handler"
            else sync_to_async(handle_tools_call)(params, context)
        )
    assert isinstance(out, dict)
    assert out.get("isError") is not True, out
    return out["structuredContent"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "call_tool", "acall_tool"])
async def test_a_provider_filled_field_is_not_offered_because_the_callers_value_is_replaced(
    route: str,
) -> None:
    server = make_server()
    server.register_tool(
        ServiceSpec(
            service=_archive_with_reason,
            validator=DataclassValidator(_Archive),
            presenter=DataclassPresenter(_Archived),
            input_data=_reason_of_record,
            atomic=False,
            permissions=[Unrestricted()],
        ),
        name="archive",
        description="Archive.",
        unknown_arguments=UnknownArguments.REJECT,
    )
    listed: Any = server.list_tools(user=None)

    out = await _served(server, route, {"reason": "caller"})

    # ``REJECT`` still admits the name, since the spec declares it; the
    assert "reason" not in listed["tools"][0]["inputSchema"]["properties"]
    assert out == {"reason": "provider"}
