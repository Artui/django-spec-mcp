"""A kernel spec's own checks answer a call before the transport charges it or reads its shape.

A kernel binding's ``permissions`` hold only its ``scopes=`` gate, and the
kernel's ``dispatch`` checks a call's arguments before it asks the spec's
checks. So the transport asks them itself first, through the kernel's own
prelude (``admit_kernel_call``): a caller they refuse is told so before the
rate limit is charged, before a task is created and before it learns which
arguments the tool takes, as the drf-services path refused it, whose spec
permissions sat in the binding's own stack. ``dispatch`` is then handed the
grant, so the checks are asked once.

Asked here of every kernel route that charges or creates work: ``tools/call``
on both handlers and in process, a task-shaped tool, ``resources/read`` on both
handlers, and what a check reading ``arguments`` sees. The xfails this
replaced, in ``test_missing_required_argument.py`` and the two
``test_an_answer_is_judged_on_*`` files, now pass there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    DispatchError,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceNotFound,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
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
    """Admits row 1 alone, refusing anything else it is handed, a malformed value included."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments.get("pk") == 1


class _BelowAHundred(PermissionCheck):
    """Admits rows below 100, reading ``pk`` as the shape check promises it: present, an int."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return int(arguments["pk"]) < 100


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


def _server(*checks: PermissionCheck, limiter: Any = None, **kwargs: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **kwargs)
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
    # answers, after the charge, as drfs answered a missing argument.
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
async def test_a_check_refusing_a_malformed_argument_is_answered_before_the_shape(
    is_async: bool,
) -> None:
    # A call whose arguments fail the shape check has no checked arguments, so
    # the check is asked on the arguments as sent. Whatever it admits is still
    # refused by the shape check, so this only decides which refusal is told.
    out = await _call(_server(_OnlyRowOne()), {"pk": "abc"}, is_async=is_async)

    assert _forbidden(out), out


@pytest.mark.parametrize("arguments", [{"pk": "abc"}, {}], ids=["malformed", "missing"])
@_ASYNC
async def test_a_check_that_cannot_read_unchecked_arguments_leaves_them_to_the_shape_check(
    arguments: dict[str, Any], is_async: bool
) -> None:
    # A check written to the kernel's promise of checked arguments raises on
    # the ones a call sent malformed or left out, a ``ValueError`` or a
    # ``KeyError``. That is no answer, so the shape check's refusal is the
    # call's, as before, rather than an internal error.
    out = await _call(_server(_BelowAHundred()), arguments, is_async=is_async)

    assert tool_error(out)["type"] == "validation_error", out


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
    # again and answers it through its own arm, after the charge, as before.
    limit = _CountingLimit()

    out = await _call(_server(_Raises(refusal), limiter=limit), {"pk": 1}, is_async=is_async)

    error = tool_error(out)
    assert error["type"] == answered_as, error
    assert str(refusal) in str(error)
    assert limit.consumed == 1


def test_call_tool_refuses_a_caller_the_spec_refuses_on_a_malformed_argument() -> None:
    with pytest.raises(NotPermitted):
        _server(_OnlyRowOne()).call_tool("get", {"pk": "abc"}, user=None)


# ---------- a task ----------


def _task_server(check: PermissionCheck, limiter: Any) -> tuple[MCPServer, _Recorder]:
    # A kernel spec takes no ``task_policy`` through ``register_tool`` yet, so
    # the binding is built by hand, which is how a task-shaped kernel tool
    # reaches ``maybe_create_task`` today.
    executor = _Recorder()
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        task_store=InMemoryTaskStore(),
        task_executor=executor,
    )
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
async def test_a_caller_the_spec_admits_gets_a_task(is_async: bool) -> None:
    limit = _CountingLimit()
    server, executor = _task_server(_Counted(admits=True), limit)

    out = await _call(server, {"pk": 1}, is_async=is_async, declares_tasks=True)

    assert out["resultType"] == "task", out
    assert len(executor.enqueued) == 1
    assert limit.consumed == 1


# ---------- resources/read ----------


def _resource_server(check: PermissionCheck, limiter: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
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
    # ``abc`` reads as no integer, so the URI's variables have no checked form
    # either, and the check is asked on them as the URI carries them.
    limit = RefusingRateLimit()

    out = await _read(_resource_server(_OnlyRowOne(), limit), "rows://abc", is_async=is_async)

    assert _forbidden(out), out
    assert limit.consumed == 0


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
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
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
