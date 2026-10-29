"""A kernel spec's own checks answer a call before the transport charges it or reads its shape.

A kernel binding's ``permissions`` hold only its ``scopes=`` gate, and the
kernel's ``dispatch`` checks a call's arguments before it asks the spec's
checks. So the transport asks them itself first, through the kernel's own
prelude (``admit_kernel_call``): a caller they refuse is told so before the
rate limit is charged, before a task is created and before it learns which
arguments the tool takes. ``dispatch`` is then handed the grant, so the
checks are asked once.

Asked here of every kernel route that charges or creates work: ``tools/call``
on both handlers and in process, a task-shaped tool, ``resources/read`` on both
handlers, and what a check reading ``arguments`` sees.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, replace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassValidator,
    DispatchError,
    InvalidArguments,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceNotFound,
    ServiceSpec,
    UnknownArguments,
    check_call,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.kernel_utils import resource_refusal
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.server.utils import UnguardedToolWarning
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.progress_dispatch import preflight_permissions
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import RefusingRateLimit, tool_error

pytestmark = pytest.mark.django_db(transaction=True)

_ASYNC = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
_PK = Parameter("pk", "integer", required=True)


class _Counted(PermissionCheck):
    """Admits or refuses every caller, counting how often it was asked."""

    def __init__(self, *, admits: bool) -> None:
        self.admits = admits
        self.asked = 0

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.asked += 1
        return self.admits


class _NotRowEight(PermissionCheck):
    """Refuses row 8, comparing the ``pk`` it is handed with an ``int``."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments["pk"] != 8


class _OnlyRowOne(PermissionCheck):
    """Admits row 1 alone, reading ``pk`` with ``.get``, which raises on no arguments."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments.get("pk") == 1


class _Raises(PermissionCheck):
    """Raises a refusal that is no denial rather than answering."""

    def __init__(self, refusal: Exception) -> None:
        self.refusal = refusal

    def has_permission(self, principal: Any, spec: Any) -> bool:
        raise self.refusal


class _CountingLimit:
    """An ``MCPRateLimit`` admitting every call, counting the calls it charged."""

    def __init__(self) -> None:
        self.consumed = 0

    def consume(self, request: Any, token: Any) -> int | None:
        self.consumed += 1
        return None


@dataclass
class _Deactivated:
    """An authenticated principal whose account is switched off."""

    pk: int = 1
    is_authenticated: bool = True
    is_active: bool = False


class _Recorder:
    """A task executor recording what it was handed."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, task_id: str) -> None:
        self.enqueued.append(task_id)


def _row(*, pk: int) -> dict[str, int]:
    return {"pk": pk}


def _get(*checks: PermissionCheck) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_row,
        reads=Parameters([_PK]),
        permissions=list(checks),
    )


def _bare_server(**kwargs: Any) -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **kwargs)


def _server(*checks: PermissionCheck, limiter: Any = None, **kwargs: Any) -> MCPServer:
    server = _bare_server(**kwargs)
    server.register_tool(
        _get(*checks), name="get", rate_limits=[limiter] if limiter is not None else ()
    )
    return server


def _ctx(server: MCPServer, *, user: Any = None, declares_tasks: bool = False) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=user, scopes=()),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2026-07-28",
        client_capabilities=(
            {"extensions": {TASKS_EXTENSION_ID: {}}} if declares_tasks else {"extensions": {}}
        ),
        tasks=server.task_store,
        task_executor=server.task_executor,
        config=server.config,
        pool_seeds=server.pool_seeds,
        conventions=server.conventions,
    )


async def _call(server: MCPServer, arguments: dict[str, Any], *, is_async: bool, **ctx: Any) -> Any:
    params: dict[str, Any] = {"name": "get", "arguments": arguments}
    if is_async:
        return await handle_tools_call_async(params, _ctx(server, **ctx))
    return await sync_to_async(handle_tools_call)(params, _ctx(server, **ctx))


def _forbidden(out: Any) -> bool:
    return isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN


# ---------- tools/call: the charge ----------


@_ASYNC
async def test_a_caller_the_spec_refuses_is_not_charged(is_async: bool) -> None:
    limit = RefusingRateLimit()

    out = await _call(_server(_Counted(admits=False), limiter=limit), {"pk": 1}, is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_a_deactivated_principal_is_not_charged(is_async: bool) -> None:
    # ``PrincipalUnavailable`` is the prelude's other denial, refused ahead of
    # every check, as dispatch refuses it.
    limit = RefusingRateLimit()

    out = await _call(
        _server(_Counted(admits=True), limiter=limit),
        {"pk": 1},
        is_async=is_async,
        user=_Deactivated(),
    )

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_an_admitted_caller_missing_an_argument_is_charged_and_told_which(
    is_async: bool,
) -> None:
    # The other half: the spec admitting the caller, the shape check still
    # answers, after the charge.
    limit = _CountingLimit()

    out = await _call(_server(_Counted(admits=True), limiter=limit), {}, is_async=is_async)

    assert tool_error(out)["detail"] == {"pk": ["This field is required."]}
    assert limit.consumed == 1


@pytest.mark.parametrize("route", ["sync", "async", "in-process"])
async def test_a_check_is_asked_once_per_call(route: str) -> None:
    # The grant the transport's own ask produced is what dispatch runs under,
    # so the spec's checks are not asked a second time inside it.
    check = _Counted(admits=True)
    server = _server(check)

    if route == "in-process":
        out: Any = (await sync_to_async(server.call_tool)("get", {"pk": 1}, user=None)).to_dict()
    else:
        out = await _call(server, {"pk": 1}, is_async=route == "async")

    assert out["structuredContent"] == {"pk": 1}
    assert check.asked == 1


def _rows() -> list[dict[str, int]]:
    return [{"pk": 1}, {"pk": 2}, {"pk": 3}]


@_ASYNC
async def test_a_paged_call_is_asked_once(is_async: bool) -> None:
    # The page is split off before the checks are asked, as ``run_kernel_tool``
    # splits it before dispatch: asked with it, the shape check would refuse
    # ``page`` and ``limit`` as unknown, the call would be judged on no
    # arguments, and dispatch would ask again.
    check = _Counted(admits=True)
    server = _bare_server()
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=[check]),
        name="get",
        paginate=True,
    )

    out = await _call(server, {"page": 1, "limit": 2}, is_async=is_async)

    assert len(out["structuredContent"]["items"]) == 2, out
    assert check.asked == 1


@_ASYNC
async def test_a_stray_argument_a_tool_ignores_is_asked_once(is_async: bool) -> None:
    # Asked under the tool's own policy: under ``REJECT`` the stray argument
    # would fail the shape check, the call would be judged on no arguments, and
    # dispatch, which drops it, would ask again.
    check = _Counted(admits=True)
    server = _bare_server()
    server.register_tool(_get(check), name="get", unknown_arguments=UnknownArguments.IGNORE)

    out = await _call(server, {"pk": 1, "stray": 1}, is_async=is_async)

    assert out["structuredContent"] == {"pk": 1}, out
    assert check.asked == 1


# ---------- tools/call: what a check reading ``arguments`` sees ----------


@_ASYNC
async def test_a_check_reads_the_arguments_as_the_shape_check_reads_them(is_async: bool) -> None:
    # Asked on the arguments the shape check produced, as dispatch asks it: the
    # string ``"8"`` reaches the selector as the int ``8``, so a check shown the
    # string as sent would compare ``"8" != 8``, admit, and the selector would
    # run on the row the check refuses.
    out = await _call(_server(_NotRowEight()), {"pk": "8"}, is_async=is_async)

    assert _forbidden(out), out


@_ASYNC
async def test_a_check_refusing_on_no_arguments_answers_a_malformed_call_before_the_shape(
    is_async: bool,
) -> None:
    # A call whose arguments fail the shape check has no checked arguments, so
    # the check is asked on none, as the listing asks it. Whatever it admits is
    # still refused by the shape check, so this only decides which refusal is
    # told, and ``.get`` on no arguments raises, which refuses.
    out = await _call(_server(_OnlyRowOne()), {"pk": "abc"}, is_async=is_async)

    assert _forbidden(out), out


@pytest.mark.parametrize(
    ("refusal", "answered_as"),
    [
        (ServiceNotFound("No such tenant."), "service_error"),
        (DispatchError("Closed for maintenance."), "validation_error"),
    ],
    ids=["service-error", "dispatch-error"],
)
@_ASYNC
async def test_a_check_raising_a_refusal_that_is_no_denial_is_answered_as_before(
    refusal: Exception, answered_as: str, is_async: bool
) -> None:
    # A ``ServiceError`` a check looking a tenant up raises, or a bare
    # ``DispatchError``: no denial, so each is left to dispatch, which raises it
    # again and answers it through its own arm, after the charge.
    limit = _CountingLimit()

    out = await _call(_server(_Raises(refusal), limiter=limit), {"pk": 1}, is_async=is_async)

    error = tool_error(out)
    assert error["type"] == answered_as, error
    assert str(refusal) in str(error)
    assert limit.consumed == 1


class _Reads(PermissionCheck):
    """Decides by ``arguments``, through ``decide``."""

    def __init__(self, decide: Any) -> None:
        self.decide = decide

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return bool(self.decide(arguments))


class _Rewrites(PermissionCheck):
    """Admits every caller, setting ``pk`` to ``1`` in any ``dict`` it is handed as ``arguments``."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        if isinstance(arguments, dict):
            arguments["pk"] = 1
        return True


def _listed(server: MCPServer) -> bool:
    # Whether ``tools/list`` shows the tool to the caller once listings are
    # filtered by permission, the question ``is_binding_listable`` answers.
    context = _ctx(server)
    return is_binding_listable(
        server.tools.get("get"),
        context.http_request,
        context.token,
        config=context.config,
        pool_seeds=context.pool_seeds,
    )


# Each shape a check reading ``arguments`` is written in, and whether the
# listing, which hands it ``None``, shows the tool.
_SHAPES = {
    "class-level-admits": (lambda: _Counted(admits=True), True),
    "class-level-refuses": (lambda: _Counted(admits=False), False),
    "none-or": (lambda: _Reads(lambda a: a is None or a["pk"] == 1), True),
    "not-none-and": (lambda: _Reads(lambda a: a is not None and a["pk"] == 1), False),
    "unguarded-index": (lambda: _Reads(lambda a: a["pk"] == 1), False),
}


@pytest.mark.parametrize("arguments", [{"pk": "abc"}, {}], ids=["malformed", "missing"])
@pytest.mark.parametrize("shape", list(_SHAPES))
@_ASYNC
async def test_a_call_failing_its_shape_is_refused_first_exactly_when_the_listing_hides_it(
    shape: str, arguments: dict[str, Any], is_async: bool
) -> None:
    # A call with no checked arguments is judged as the listing judges, on no
    # arguments at all, so a caller learns which argument it left out exactly
    # when the tool is listed to it, and is otherwise refused uncharged.
    build, listed = _SHAPES[shape]
    limit = _CountingLimit()
    server = _server(build(), limiter=limit)

    out = await _call(server, arguments, is_async=is_async)

    assert _listed(server) is listed
    assert _forbidden(out) is not listed, out
    assert limit.consumed == (1 if listed else 0)
    if listed:
        assert tool_error(out)["type"] == "validation_error", out


@pytest.mark.parametrize("arguments", [{"pk": "abc"}, {}], ids=["malformed", "missing"])
@_ASYNC
async def test_a_misconfigured_check_fails_a_call_failing_its_shape_as_it_fails_the_listing(
    arguments: dict[str, Any], is_async: bool
) -> None:
    server = _server(_Raises(ImproperlyConfigured("Declare the method with def.")))

    with pytest.raises(ImproperlyConfigured):
        _listed(server)
    with pytest.raises(ImproperlyConfigured):
        await _call(server, arguments, is_async=is_async)


@pytest.mark.parametrize(
    "decide",
    [lambda a: a.get("pk") == 8, lambda a: a.get("pk") != 8],
    ids=["admits-row-eight", "refuses-row-eight"],
)
@_ASYNC
async def test_a_call_missing_an_argument_is_refused_by_a_check_the_listing_hides_it_for(
    decide: Any, is_async: bool
) -> None:
    # ``"8"`` without the required ``name``: the check is never shown the
    # string, which would read as a row it admits in one case and one it
    # refuses in the other. ``.get`` on no arguments raises, so the listing
    # hides the tool, and the call is refused uncharged in both.
    limit = _CountingLimit()
    server = _bare_server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk, name: {"pk": pk},
            reads=Parameters([_PK, Parameter("name", "string", required=True)]),
            permissions=[_Reads(decide)],
        ),
        name="get",
        rate_limits=[limit],
    )

    out = await _call(server, {"pk": "8"}, is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0
    assert _listed(server) is False


@_ASYNC
async def test_a_check_never_writes_the_arguments_a_call_sent(is_async: bool) -> None:
    # A check is handed no arguments for a call failing its shape, so nothing
    # it writes reaches the call, which the shape check still refuses.
    out = await _call(_server(_Rewrites()), {"pk": "abc"}, is_async=is_async)

    assert tool_error(out)["type"] == "validation_error", out


@dataclass
class _Line:
    number: str
    amount_cents: int

    def __post_init__(self) -> None:
        if self.amount_cents < 0:
            raise ValueError("Ensure this value is greater than or equal to 0.")


def _issue(*, data: list[_Line]) -> list[_Line]:
    return list(data)


@pytest.mark.parametrize(("streamed", "asks"), [(False, 2), (True, 3)], ids=["plain", "streamed"])
@_ASYNC
async def test_a_many_call_with_a_refused_row_is_asked_by_its_prelude_and_by_dispatch(
    streamed: bool, asks: int, is_async: bool
) -> None:
    # ``check_call`` asks the checks before it raises the first row's refusal,
    # which hands back no grant, and dispatch, which is what reports the
    # second row's Validator refusal beside it, asks again. The answer is
    # dispatch's, both rows named. A stream's pre-flight asks once more.
    check = _Counted(admits=True)
    server = _bare_server()
    server.register_tool(
        ServiceSpec(
            service=_issue,
            many=True,
            validator=DataclassValidator(_Line),
            permissions=[check],
        ),
        name="get",
    )
    rows = [{"number": "A-1", "amount_cents": "x"}, {"number": "A-2", "amount_cents": -5}]
    if streamed:
        params = {"name": "get", "arguments": {"items": rows}}
        assert (
            await sync_to_async(preflight_permissions)("tools/call", params, _ctx(server)) is None
        )

    out = await _call(server, {"items": rows}, is_async=is_async)

    assert sorted(tool_error(out)["detail"]["items"]) == ["0", "1"], out
    assert check.asked == asks


class _TenantOne(PermissionCheck):
    """Admits tenant 1 alone, indexing ``arguments`` with no guard, so it raises on none."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        self.seen.append(arguments)
        return arguments["tenant"] == 1


class _Recording(PermissionCheck):
    """Admits everyone, recording the ``arguments`` it is handed."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        self.seen.append(arguments)
        return True


@pytest.mark.parametrize("arguments", [None, [1], "x"], ids=["null", "list", "string"])
def test_a_streamed_call_with_no_arguments_object_is_judged_on_none_sent(
    arguments: Any,
) -> None:
    # The pre-flight runs before the handler validates ``arguments``, so it
    # meets one that is ``null`` or not an object. It judges those as
    # delivering nothing, as the handler reads an absent one, rather than
    # handing the check what no check can index, and leaves the handler to
    # name the fault.
    check = _Recording()
    server = _bare_server()
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {}, permissions=[check]),
        name="get",
    )

    params = {"name": "get", "arguments": arguments}
    assert preflight_permissions("tools/call", params, _ctx(server)) is None
    assert check.seen == [{}]


def _tenant_bulk(check: PermissionCheck) -> ServiceSpec:
    return ServiceSpec(
        service=lambda *, data, tenant: data,
        many=True,
        validator=DataclassValidator(_Line),
        reads=Parameters([Parameter("tenant", "integer", required=True)]),
        permissions=[check],
    )


_BAD_ROW = [{"number": "A-1", "amount_cents": "x"}]


def test_check_call_raises_a_refused_row_only_after_admitting_the_top_level() -> None:
    # The kernel behaviour ``admit_kernel_call`` reads a refused row by: raised
    # after the class-level check admitted the checked top level, and keyed by
    # index under ``many_argument`` alone, where a failed top level is raised
    # before that check, without the rows. If either changes, this fails.
    check = _Counted(admits=True)
    spec = _tenant_bulk(check)

    with pytest.raises(InvalidArguments) as row:
        check_call(spec, principal=None, arguments={"tenant": 1, "items": _BAD_ROW})
    assert row.value.detail == {"items": {0: {"amount_cents": ["Expected an integer."]}}}
    assert check.asked == 1

    with pytest.raises(InvalidArguments) as shape:
        check_call(spec, principal=None, arguments={"items": _BAD_ROW})
    assert shape.value.detail == {"tenant": ["This field is required."]}
    assert check.asked == 1


@pytest.mark.parametrize(
    ("tenant", "admitted"), [(1, True), (2, False)], ids=["admitted", "refused"]
)
@pytest.mark.parametrize("streamed", [False, True], ids=["plain", "streamed"])
@_ASYNC
async def test_a_many_call_with_a_refused_row_is_answered_on_its_checked_top_level(
    tenant: int, admitted: bool, streamed: bool, is_async: bool
) -> None:
    # The checks admitted or refused the checked top level before the row was
    # refused, so that answer stands: an admitted caller is told the row after
    # the charge, never judged again on no arguments, which this check, reading
    # ``tenant`` unguarded, would refuse.
    check = _TenantOne()
    limit = _CountingLimit()
    server = _bare_server()
    server.register_tool(_tenant_bulk(check), name="get", rate_limits=[limit])
    arguments = {"tenant": tenant, "items": _BAD_ROW}
    if streamed:
        params = {"name": "get", "arguments": arguments}
        refused = await sync_to_async(preflight_permissions)("tools/call", params, _ctx(server))
        assert (refused is None) is admitted

    out = await _call(server, arguments, is_async=is_async)

    if admitted:
        assert tool_error(out)["detail"] == {
            "items": {"0": {"amount_cents": ["Expected an integer."]}}
        }, out
        assert limit.consumed == 1
    else:
        assert _forbidden(out), out
        assert limit.consumed == 0
    assert None not in check.seen


@_ASYNC
async def test_a_refused_row_under_a_named_many_argument_keeps_its_admission(
    is_async: bool,
) -> None:
    # The rows travel under the argument ``many_argument`` names, ``lines`` here,
    # so the refused row is read there: a guard that looked under ``items`` alone
    # would read this tree as a failed shape and judge the call on no arguments,
    # which this check, indexing ``tenant`` unguarded, refuses.
    limit = _CountingLimit()
    server = _bare_server()
    server.register_tool(
        ServiceSpec(
            service=lambda *, data, tenant: data,
            many=True,
            many_argument="lines",
            validator=DataclassValidator(_Line),
            reads=Parameters([Parameter("tenant", "integer", required=True)]),
            permissions=[_TenantOne()],
        ),
        name="get",
        rate_limits=[limit],
    )

    out = await _call(server, {"tenant": 1, "lines": _BAD_ROW}, is_async=is_async)

    assert tool_error(out)["detail"] == {
        "lines": {"0": {"amount_cents": ["Expected an integer."]}}
    }, out
    assert limit.consumed == 1


@_ASYNC
async def test_a_check_raising_a_refusal_shaped_like_a_refused_row_keeps_no_admission(
    is_async: bool,
) -> None:
    # A check raising ``InvalidArguments`` keyed by row index under the many
    # argument is no refused row: the kernel marks the one ``check_call`` raises
    # after admitting the top level (``after_authorize``), and this one is not
    # marked. So it is answered as any refusal a check raises is, judged on no
    # arguments, which this check cannot answer, and refused before the charge.
    # Read off the tree alone, it kept an admission it was never given and was
    # charged.
    limit = _CountingLimit()
    tree = InvalidArguments({"items": {0: {"amount_cents": ["Not this row."]}}})
    server = _bare_server()
    server.register_tool(_tenant_bulk(_Raises(tree)), name="get", rate_limits=[limit])
    rows = [{"number": "A-1", "amount_cents": 1}]

    out = await _call(server, {"tenant": 1, "items": rows}, is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_an_array_argument_failing_at_an_index_is_no_refused_row(is_async: bool) -> None:
    # A spec that is not ``many`` fails an array argument's item at its index,
    # under the argument's name, ``items`` here: a failed shape all the same,
    # judged on no arguments, which this check refuses.
    limit = _CountingLimit()
    server = _bare_server()
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, items: {"pk": 1},
            reads=Parameters([Parameter("items", "array", items="integer", required=True)]),
            permissions=[_Reads(lambda a: a is not None)],
        ),
        name="get",
        rate_limits=[limit],
    )

    out = await _call(server, {"items": ["x"]}, is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_a_many_list_that_is_no_list_is_a_failed_shape(is_async: bool) -> None:
    # Its refusal sits under ``many_argument`` too, as a message rather than
    # rows by index, so it is judged on no arguments, which ``_TenantOne``
    # cannot answer.
    limit = _CountingLimit()
    server = _bare_server()
    server.register_tool(_tenant_bulk(_TenantOne()), name="get", rate_limits=[limit])

    out = await _call(server, {"tenant": 1, "items": "no"}, is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


class _ScopedToAcme(PermissionCheck):
    """Admits every class-level question, and a create's scope for the ``tenant`` seed ``acme``."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any, *, tenant: str) -> bool:
        return tenant == "acme"


def test_a_creates_scope_is_judged_on_the_servers_own_seeds() -> None:
    # The scope question of a create, asked on no arguments, is offered the
    # server's pool seeds, as the class-level one is: a check naming a seed
    # only this server registers is handed it, and admits.
    server = _acme()
    server.register_tool(ServiceSpec(service=lambda: {}, permissions=[_ScopedToAcme()]), name="get")

    assert _listed(server) is True


class _NamesTheTenant(PermissionCheck):
    """Admits the ``tenant`` seed ``acme`` alone, a seed only its server registers."""

    def has_permission(self, principal: Any, spec: Any, *, tenant: str) -> bool:
        return tenant == "acme"


class _NamesTheAction(PermissionCheck):
    """Admits a call whose ``call_context`` names the ``get`` action."""

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        return call_context.get("action") == "get"


def _acme(**kwargs: Any) -> MCPServer:
    return _bare_server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"), **kwargs)


def _strict() -> MCPServer:
    # The test settings turn ``REQUIRE_TOOL_PERMISSIONS`` off, so only a server
    # turning it on tells its own setting apart from the settings' default.
    return _acme(config=replace(build_mcp_config(), require_tool_permissions=True))


def _marked_elsewhere(register: Any) -> Any:
    """A binding a server with the setting off marked unguarded, as ``register`` builds it."""
    lenient = _acme(config=replace(build_mcp_config(), require_tool_permissions=False))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnguardedToolWarning)
        return register(lenient)


def _unguarded() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_row, reads=Parameters([_PK]))


@pytest.mark.parametrize("case", ["seeds", "context"])
@_ASYNC
async def test_a_call_failing_its_shape_is_judged_with_what_its_call_carries(
    case: str, is_async: bool
) -> None:
    # Judged on no arguments, but with the server's seeds and the call's
    # context, as the call is: each admits here, so the caller is told the
    # shape after the charge.
    limit = _CountingLimit()
    server = _acme()
    check = _NamesTheTenant() if case == "seeds" else _NamesTheAction()
    server.register_tool(_get(check), name="get", rate_limits=[limit])

    out = await _call(server, {"pk": "abc"}, is_async=is_async)

    assert tool_error(out)["type"] == "validation_error", out
    assert limit.consumed == 1


@_ASYNC
async def test_a_call_failing_its_shape_is_judged_under_the_answering_servers_setting(
    is_async: bool,
) -> None:
    # A binding marked unguarded elsewhere is granted no call by a server with
    # the setting on, so it refuses every caller, uncharged.
    limit = _CountingLimit()
    server = _strict()
    server.tools.register(
        _marked_elsewhere(
            lambda lenient: lenient.register_tool(_unguarded(), name="get", rate_limits=[limit])
        )
    )

    out = await _call(server, {"pk": "abc"}, is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


@pytest.mark.parametrize("case", ["seeds", "setting"])
def test_a_completion_is_judged_with_the_servers_seeds_and_setting(case: str) -> None:
    if case == "seeds":
        server = _acme()
        server.register_resource(
            _get(_NamesTheTenant()),
            name="row",
            uri_template="rows://{pk}",
            completions={"pk": lambda: ["1"]},
        )
    else:
        server = _strict()
        server.resources.register(
            _marked_elsewhere(
                lambda lenient: lenient.register_resource(
                    _unguarded(),
                    name="row",
                    uri_template="rows://{pk}",
                    completions={"pk": lambda: ["1"]},
                )
            )
        )
    params = {
        "ref": {"type": "ref/resource", "uri": "rows://{pk}"},
        "argument": {"name": "pk", "value": ""},
    }

    out: Any = handle_completion_complete(params, _ctx(server))

    if case == "seeds":
        assert out["completion"]["values"] == ["1"], out
    else:
        assert _forbidden(out), out


class _AliceWithHerToken(PermissionCheck):
    """Admits a signed-in ``alice`` whose call carries her token and the resource's name."""

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        request = call_context.get("request")
        return (
            getattr(principal, "username", None) == "alice"
            and getattr(request, "mcp_token", None) is not None
            and call_context.get("action") == "invoice"
        )


@pytest.mark.parametrize(
    ("user", "admitted"), [("alice", True), (None, False)], ids=["signed-in", "anonymous"]
)
def test_a_completion_is_judged_with_the_callers_principal_and_context(
    client: Client, user: str | None, admitted: bool
) -> None:
    # The completion asks the spec as the read does: for the token's user (the
    # principal is ``None`` without ``principal_of(context.token.user)``), with a
    # call context (``call_context=None`` fails it) whose request carries the
    # token (``carry_token`` dropped fails it) and the binding's name as action.
    get_user_model().objects.create_user("alice")
    server = MCPServer(
        name="t", auth_backend=HeaderAuthBackend(), session_store=InMemorySessionStore()
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: {"pk": pk},
            reads=Parameters([_PK]),
            permissions=[_AliceWithHerToken()],
        ),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["1"]},
    )

    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client, user=user)
        caller.initialize()
        body = caller.post(
            "completion/complete",
            {
                "ref": {"type": "ref/resource", "uri": "invoices://{pk}"},
                "argument": {"name": "pk", "value": ""},
            },
        ).json()

    if admitted:
        assert body["result"]["completion"]["values"] == ["1"], body
    else:
        assert body["error"]["code"] == JsonRpcErrorCode.FORBIDDEN, body


@_ASYNC
async def test_an_always_listed_tool_still_refuses_a_call_failing_its_shape(is_async: bool) -> None:
    # ``always_listed`` decides the listing, not who may call: the check still
    # cannot answer on no arguments, so the call is refused uncharged.
    limit = _CountingLimit()
    server = _bare_server()
    server.register_tool(
        _get(_Reads(lambda a: a["pk"] == 1)),
        name="get",
        always_listed=True,
        rate_limits=[limit],
    )

    out = await _call(server, {"pk": "abc"}, is_async=is_async)

    assert _listed(server) is True
    assert _forbidden(out), out
    assert limit.consumed == 0


def test_call_tool_refuses_a_malformed_call_a_check_refuses_on_no_arguments() -> None:
    with pytest.raises(NotPermitted):
        _server(_OnlyRowOne()).call_tool("get", {"pk": "abc"}, user=None)


# ---------- a task ----------


def _task_server(check: PermissionCheck, limiter: Any) -> tuple[MCPServer, _Recorder]:
    executor = _Recorder()
    server = _bare_server(task_store=InMemoryTaskStore(), task_executor=executor)
    server.tools.register(
        KernelToolBinding(
            name="get",
            description="Get.",
            spec=_get(check),
            task_policy=TaskPolicy.OPTIONAL,
            rate_limits=(limiter,),
        )
    )
    return server, executor


@_ASYNC
async def test_a_caller_the_spec_refuses_gets_no_task(is_async: bool) -> None:
    # As the streamed route answers it: no durable work is queued for a call
    # its worker would refuse, and nothing is charged.
    limit = RefusingRateLimit()
    server, executor = _task_server(_Counted(admits=False), limit)

    out = await _call(server, {"pk": 1}, is_async=is_async, declares_tasks=True)

    assert _forbidden(out), out
    assert executor.enqueued == []
    assert limit.consumed == 0


@_ASYNC
async def test_a_caller_the_spec_admits_gets_a_task_its_worker_asks_again(is_async: bool) -> None:
    check = _Counted(admits=True)
    limit = _CountingLimit()
    server, executor = _task_server(check, limit)

    out = await _call(server, {"pk": 1}, is_async=is_async, declares_tasks=True)

    assert out["resultType"] == "task", out
    assert len(executor.enqueued) == 1
    assert limit.consumed == 1
    assert check.asked == 1

    # The worker's replay asks the spec again, in full, and is not charged.
    await sync_to_async(server.run_task)(executor.enqueued[0])

    record: Any = server.task_store.get(executor.enqueued[0])
    assert record.status.value == "completed", record
    assert check.asked == 2
    assert limit.consumed == 1


# ---------- resources/read ----------


def _resource_server(check: PermissionCheck, limiter: Any) -> MCPServer:
    server = _bare_server()
    server.register_resource(
        _get(check), name="row", uri_template="rows://{pk}", rate_limits=[limiter]
    )
    return server


async def _read(server: MCPServer, uri: str, *, is_async: bool, **ctx: Any) -> Any:
    params: dict[str, Any] = {"uri": uri}
    if is_async:
        return await handle_resources_read_async(params, _ctx(server, **ctx))
    return await sync_to_async(handle_resources_read)(params, _ctx(server, **ctx))


@_ASYNC
async def test_a_reader_the_spec_refuses_is_not_charged(is_async: bool) -> None:
    limit = RefusingRateLimit()

    out = await _read(
        _resource_server(_Counted(admits=False), limit), "rows://1", is_async=is_async
    )

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_a_deactivated_reader_is_not_charged(is_async: bool) -> None:
    limit = RefusingRateLimit()

    out = await _read(
        _resource_server(_Counted(admits=True), limit),
        "rows://1",
        is_async=is_async,
        user=_Deactivated(),
    )

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_a_reader_refused_on_a_malformed_variable_is_told_so(is_async: bool) -> None:
    # ``abc`` reads as no integer, so the URI's variables have no checked form,
    # and the call is judged on no arguments, where ``.get`` raises and refuses.
    limit = RefusingRateLimit()

    out = await _read(_resource_server(_OnlyRowOne(), limit), "rows://abc", is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


@_ASYNC
async def test_an_admitted_reader_of_a_malformed_variable_is_charged_and_told_which(
    is_async: bool,
) -> None:
    # The judgment admits, so the read is charged and then refused on the
    # variable, as an admitted tool call is told which argument is wrong after
    # its charge: ``-32602`` with the kernel's detail in ``data``.
    limit = _CountingLimit()

    out = await _read(
        _resource_server(_Counted(admits=True), limit), "rows://abc", is_async=is_async
    )

    assert isinstance(out, JsonRpcError), out
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert out.message == "Invalid arguments"
    assert list(out.data["detail"]) == ["pk"]
    assert limit.consumed == 1


@_ASYNC
def test_an_admitted_reader_of_a_malformed_variable_is_told_which_over_http(
    is_async: bool, client: Client
) -> None:
    ran: list[int] = []

    def selector(*, pk: int) -> dict[str, int]:
        ran.append(pk)
        return {"pk": pk}

    limit = _CountingLimit()
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend(), session_store=None)
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            reads=Parameters([_PK]),
            permissions=[_Counted(admits=True)],
        ),
        name="row",
        uri_template="rows://{pk}",
        rate_limits=[limit],
    )

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        caller = KernelClient(client)
        caller.initialize()
        response = caller.post("resources/read", {"uri": "rows://abc"})

    body = response.json()
    assert response.status_code == 200, body
    assert body["error"]["code"] == -32602, body
    assert body["error"]["message"] == "Invalid arguments"
    assert body["error"]["message"] != INTERNAL_ERROR_MESSAGE
    assert body["error"]["data"]["detail"]["pk"], body
    assert limit.consumed == 1
    assert ran == []


@pytest.mark.parametrize("include_value", [False, True], ids=["bare", "with-value"])
def test_a_reader_told_its_arguments_are_invalid_gets_the_detail_as_json_would_write_it(
    include_value: bool,
) -> None:
    # A many call's node mixes integer row indices with ``non_field_errors``, which
    # a sorted-key encoder refuses; the data is the tree with its keys as strings.
    scene = KernelRefusalScene(
        arguments={"pk": "abc"},
        config=replace(build_mcp_config(), include_validation_value=include_value),
        conventions=AgentConventions(),
    )
    refusal = InvalidArguments({"rows": {1: {"pk": ["Bad."]}, "non_field_errors": ["Too many."]}})

    out = resource_refusal(refusal, scene)

    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert out.data["detail"] == {
        "rows": {"1": {"pk": ["Bad."]}, "non_field_errors": ["Too many."]}
    }
    # The kernel's reading order is kept: the rows by index, then the list's message.
    assert list(out.data["detail"]["rows"]) == ["1", "non_field_errors"]
    assert out.data.get("value") == ({"pk": "abc"} if include_value else None)
    json.dumps(out.to_dict())


def test_a_refusal_that_is_no_invalid_argument_is_still_raised_on_by_a_read() -> None:
    # The arm is ``InvalidArguments`` alone: a bare ``DispatchError`` stays the
    # transport's internal error.
    scene = KernelRefusalScene(
        arguments={}, config=build_mcp_config(), conventions=AgentConventions()
    )

    with pytest.raises(DispatchError):
        resource_refusal(DispatchError("no"), scene)


@_ASYNC
async def test_an_admitted_reader_is_charged_once_and_read_once(is_async: bool) -> None:
    check = _Counted(admits=True)
    limit = _CountingLimit()

    out = await _read(_resource_server(check, limit), "rows://1", is_async=is_async)

    assert json.loads(out["contents"][0]["text"]) == {"pk": 1}, out
    assert limit.consumed == 1
    assert check.asked == 1


class _NotArchived(PermissionCheck):
    """Refuses archived rows, comparing ``archived`` with ``True`` as the read hands it over."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments["archived"] is not True


def _archived(*, archived: bool) -> dict[str, bool]:
    return {"archived": archived}


@_ASYNC
async def test_a_reader_is_judged_on_the_variables_the_read_dispatches(is_async: bool) -> None:
    # A URI carries ``true`` as text, which the shape check reads as no boolean
    # and the read's ``coerce_flat`` reads as ``True``. Asked on the text, this
    # check would admit the caller, the limit would be charged and the read's
    # own dispatch would then refuse ``True``: the check is asked on the
    # variables the read dispatches, so it refuses before the charge.
    limit = RefusingRateLimit()
    server = _bare_server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_archived,
            reads=Parameters([Parameter("archived", "boolean", required=True)]),
            permissions=[_NotArchived()],
        ),
        name="flag",
        uri_template="flags://{archived}",
        rate_limits=[limit],
    )

    out = await _read(server, "flags://true", is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0
