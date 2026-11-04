"""Every reading of a tool's spec during a call is handed that call's ``call_context``.

A Validator may declare ``parameters(self, *, call_context=None)``, and the
kernel's ``apply_server_arguments`` and ``server_owned_keys`` read the spec as it
declares itself to the dict they are handed, ``None`` reading the declaration
made to no call. Each ``tools/call`` route hands them the call's dict: the bound
value is checked against what the spec declares to the call, and the names an
answer may come back under are the call's.

``TypedByTheCall`` records what each reader is handed, which is what tells the
sites apart: a later reader handed the call's dict does not undo an earlier one
handed ``None``, so each route is held by the record, and the bound value's
refusal holds the rule's effect.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    AdditionalInputRequired,
    DataclassValidator,
    Parameters,
    PoolSeeds,
    ServerArguments,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.elicitation.conftest import DeleteRows
from tests.elicitation.conftest import context as asking_context
from tests.tasks.conftest import RecordingExecutor
from tests.tasks.conftest import context as task_context
from tests.utils import TypedByTheCall, call_context, make_server

TOOL: str = "projects.touch"


class _Ran:
    """The service: records the ``call_context`` dispatch hands it."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def __call__(self, *, project: Any, call_context: dict[str, Any]) -> dict[str, Any]:
        self.contexts.append(call_context)
        return {"project": project}


def _server(bound: Any, **overrides: Any) -> tuple[MCPServer, TypedByTheCall, _Ran]:
    validator = TypedByTheCall(TOOL)
    ran = _Ran()
    server = make_server(**overrides)
    server.register_tool(
        ServiceSpec(service=ran, validator=validator, permissions=[Unrestricted()], atomic=False),
        name=TOOL,
        server_arguments=ServerArguments(values={"project": bound}),
        task_policy=TaskPolicy.REQUIRED if "task_store" in overrides else TaskPolicy.FORBIDDEN,
    )
    validator.handed.clear()
    return server, validator, ran


async def _call(params: dict[str, Any], ctx: MCPCallContext, is_async: bool) -> Any:
    if is_async:
        return await handle_tools_call_async(params, ctx)
    return await sync_to_async(handle_tools_call)(params, ctx)


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_bound_value_the_call_s_declaration_refuses_is_the_server_s_mistake(
    is_async: bool,
) -> None:
    """``"x"`` passes the declaration made to no call, which registration reads,
    and the call's own refuses it. Handed the call's dict the kernel's rule
    raises ``ImproperlyConfigured``; handed ``None`` the value reaches dispatch,
    which tells the caller an argument it never sent is invalid."""
    server, _validator, ran = _server("x")
    with pytest.raises(ImproperlyConfigured, match="binds a value its parameter refuses"):
        await _call({"name": TOOL, "arguments": {}}, call_context(server), is_async)
    assert ran.contexts == []


def test_call_tool_checks_a_bound_value_against_the_call_s_declaration() -> None:
    """The in-process ``call_tool`` lays the bound value under the call's dict too."""
    server, _validator, ran = _server("x")
    with pytest.raises(ImproperlyConfigured, match="binds a value its parameter refuses"):
        server.call_tool(TOOL, {}, user=None)
    assert ran.contexts == []


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_every_reader_in_a_call_is_handed_the_call_s_dict(is_async: bool) -> None:
    """Two dicts, both the call's: the one ``resolve_tool_call`` lays the bound
    value under before the task decision, and the one the re-apply after a
    retry's answers, the admission and dispatch share. Handed ``None`` at the
    re-apply or by ``admitted_arguments``, the record holds a ``None``; handed a
    dict of its own at the re-apply, the record holds three."""
    server, validator, ran = _server(7)
    out = await _call({"name": TOOL, "arguments": {}}, call_context(server), is_async)
    assert out["structuredContent"] == {"project": 7}, out
    resolved, dispatched = validator.distinct()
    assert dispatched is ran.contexts[0]
    assert resolved == dispatched
    assert dispatched["action"] == TOOL


def test_call_tool_hands_every_reader_the_dict_it_dispatches_with() -> None:
    """One dict: the bound value is laid under the one dispatch is handed, built
    from the copy of the request the call runs on."""
    server, validator, ran = _server(7)
    result = server.call_tool(TOOL, {}, user=None)
    assert result.structured_content == {"project": 7}
    (only,) = validator.distinct()
    assert only is ran.contexts[0]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_task_is_judged_under_the_call_s_dict(is_async: bool) -> None:
    """Task creation lays the bound value again after a retry's answers and asks
    the checks with the dict it laid it under."""
    store = InMemoryTaskStore()
    server, validator, _ran = _server(7, task_store=store, task_executor=RecordingExecutor(store))
    out = await _call({"name": TOOL, "arguments": {}}, task_context(server), is_async)
    assert out["resultType"] == "task", out
    resolved, admitted = validator.distinct()
    assert resolved == admitted
    assert admitted["action"] == TOOL


@dataclass
class _Rows(DeleteRows):
    tenant: str = ""


class _TenantOnlyToACall(DataclassValidator):
    """Declares a ``tenant`` field to a call of ``rows.delete``, and none to no call.

    ``tenant`` is a registered seed's name, which ``server_owned_keys`` counts
    the server's unless the Validator declares it a field: the call's
    declaration does, so the field is the caller's to answer.
    """

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        declared = super().parameters()
        if call_context is None or call_context["action"] != "rows.delete":
            return Parameters([each for each in declared if each.name != "tenant"])
        return declared


def _asks_for_the_tenant(**_fields: Any) -> dict[str, Any]:
    raise AdditionalInputRequired("Which tenant?", schema={"tenant": {"type": "string"}})


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_question_for_a_field_named_like_a_seed_is_asked(is_async: bool) -> None:
    """``admitted_arguments`` subtracts ``server_owned_keys`` read under the
    call's dict, where ``tenant`` is a field. Read under ``None`` it is the
    seed's, so the question would be refused as asking for a name the server
    fills, ``-32603``."""
    server = make_server(pool_seeds=PoolSeeds().extend(tenant=lambda: "acme"))
    server.register_tool(
        ServiceSpec(
            service=_asks_for_the_tenant,
            validator=_TenantOnlyToACall(_Rows),
            permissions=[Unrestricted()],
            atomic=False,
        ),
        name="rows.delete",
    )
    ctx = replace(asking_context(server), pool_seeds=server._pool_seeds)
    out = await _call({"name": "rows.delete", "arguments": {"count": 1}}, ctx, is_async)
    assert not isinstance(out, JsonRpcError), out
    assert out["resultType"] == "input_required"
