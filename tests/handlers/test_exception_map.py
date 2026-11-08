"""``MCPServer(exception_map=...)``: how a tool answers a project's own exception.

Each test names the route it drives, because the routes do not share a catch: the
wire's two ``run_kernel_tool`` siblings, the in-process ``call_kernel_tool``, and the
admission and the presenter that run around them.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    AdditionalInputRequired,
    DataclassValidator,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    Presenter,
    PrincipalUnavailable,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
    Validator,
)

from django_spec_mcp import ExceptionHandler
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.answer_mapped_exception import answer_mapped_exception
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.output import build_error_tool_result, build_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.subscriptions.in_memory_subscription_broker import InMemorySubscriptionBroker
from django_spec_mcp.subscriptions.utils import topic_for_resource
from tests.utils import call_context, make_server, tool_error

pytestmark = pytest.mark.django_db(transaction=True)

TOOL = "orders.place"


@dataclass
class _Account:
    """What a service asking for an ``account`` declares, so the answer is an argument it takes."""

    account: str = ""


class TenantGone(Exception):
    pass


class ArchivedTenant(TenantGone):
    pass


class FrozenAccount(ServiceError):
    """A project's own refusal, under a kernel family."""


def _handler(error_type: str) -> ExceptionHandler:
    def handle(exc: Exception) -> ToolResult:
        return build_error_tool_result(str(exc), error_type=error_type)

    return handle


GONE = _handler("tenant_gone")
ROUTES = ["wire-sync", "wire-async", "call_tool", "acall_tool"]


async def _call(route: str, server: Any, **context: Any) -> Any:
    """``TOOL`` called on ``route``, the answer as the wire's dict or a ``JsonRpcError``."""
    params = {"name": TOOL, "arguments": {}}
    if route == "wire-sync":
        return await sync_to_async(handle_tools_call)(params, call_context(server, **context))
    if route == "wire-async":
        return await handle_tools_call_async(params, call_context(server, **context))
    if route == "acall_tool":
        return await server.acall_tool(TOOL, {}, user=None)
    return (await sync_to_async(server.call_tool)(TOOL, {}, user=None)).to_dict()


def _server(raises: BaseException, *, exception_map: Any = None, **overrides: Any) -> Any:
    def place() -> None:
        raise raises

    server = make_server(exception_map=exception_map, **overrides)
    server.register_tool(ServiceSpec(service=place, permissions=[Unrestricted()]), name=TOOL)
    return server


# ----- a mapped exception


@pytest.mark.parametrize("route", ROUTES)
async def test_a_mapped_exception_answers_as_its_handler_says(route: str) -> None:
    """A tool result, on every route, never a protocol error."""
    server = _server(TenantGone("no such tenant"), exception_map={TenantGone: GONE})
    out = await _call(route, server)
    assert out["isError"] is True
    assert tool_error(out) == {"type": "tenant_gone", "message": "no such tenant"}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("raised", "answered_by"),
    [(ArchivedTenant, "archived"), (TenantGone, "gone")],
    ids=["subclass", "base"],
)
async def test_the_most_specific_type_wins(route: str, raised: type, answered_by: str) -> None:
    """The base registered first, so a walk of the mapping's own order, or one by
    ``isinstance``, hands the subclass to the base's handler."""
    exception_map = {TenantGone: _handler("gone"), ArchivedTenant: _handler("archived")}
    assert next(iter(exception_map)) is TenantGone
    out = await _call(route, _server(raised("x"), exception_map=exception_map))
    assert tool_error(out)["type"] == answered_by


async def test_a_subclass_with_no_entry_is_answered_by_its_base() -> None:
    server = _server(ArchivedTenant("x"), exception_map={TenantGone: GONE})
    assert tool_error(await _call("wire-async", server))["type"] == "tenant_gone"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_project_subclass_of_a_kernel_refusal_reaches_its_handler(route: str) -> None:
    """The kernel's ``ServiceError`` row would answer it, so an entry below a row wins."""
    server = _server(FrozenAccount("frozen"), exception_map={FrozenAccount: _handler("frozen")})
    assert tool_error(await _call(route, server))["type"] == "frozen"


async def test_a_kernel_refusal_a_project_names_answers_as_its_handler_says() -> None:
    server = _server(ServiceNotFound("nope"), exception_map={ServiceNotFound: _handler("mine")})
    assert tool_error(await _call("wire-async", server))["type"] == "mine"


# ----- what the map leaves alone


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "exception_map", [None, {KeyError: GONE}], ids=["no-map", "an-unrelated-map"]
)
async def test_an_unmapped_exception_is_raised_as_it_is_today(
    route: str, exception_map: Any
) -> None:
    """The very object, for the transport's backstop to log and answer ``-32603``."""
    raised = TenantGone("x")
    with pytest.raises(TenantGone) as caught:
        await _call(route, _server(raised, exception_map=exception_map))
    assert caught.value is raised


EVERY_BROAD_MAP = pytest.mark.parametrize(
    "exception_map",
    [None, {KeyError: GONE}, {Exception: GONE}, {ServiceError: GONE}],
    ids=["no-map", "unrelated", "exception", "service-error"],
)


@EVERY_BROAD_MAP
async def test_a_broad_entry_leaves_a_service_s_validation_refusal_alone(
    exception_map: Any,
) -> None:
    """Each refusal below has a row of its own in ``KERNEL_REFUSALS``, which is met
    before ``ServiceError`` or ``Exception`` on the way up its MRO."""
    server = _server(ServiceValidationError("bad"), exception_map=exception_map)
    assert tool_error(await _call("wire-sync", server))["type"] == "validation_error"


@EVERY_BROAD_MAP
async def test_a_broad_entry_leaves_a_denial_alone(exception_map: Any) -> None:
    out = await _call("wire-sync", _server(NotPermitted(), exception_map=exception_map))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32006


@EVERY_BROAD_MAP
async def test_a_broad_entry_leaves_a_request_for_input_alone(exception_map: Any) -> None:
    def place(*, account: str = "") -> None:
        raise AdditionalInputRequired("Which?", schema={"account": {"type": "string"}})

    server = make_server(exception_map=exception_map)
    server.register_tool(
        ServiceSpec(
            service=place, validator=DataclassValidator(_Account), permissions=[Unrestricted()]
        ),
        name=TOOL,
    )
    # This context declares no elicitation, so MRTR's degraded answer: still that row's.
    assert tool_error(await _call("wire-sync", server))["type"] == "input_required"


@pytest.mark.parametrize("exception_map", [None, {KeyError: GONE}], ids=["no-map", "unrelated"])
async def test_a_service_error_still_answers_as_a_service_error(exception_map: Any) -> None:
    out = await _call("wire-sync", _server(ServiceError("closed"), exception_map=exception_map))
    assert tool_error(out) == {"type": "service_error", "message": "closed"}


async def test_a_service_error_entry_reaches_what_only_its_row_answers() -> None:
    """``ServiceNotFound`` has no row of its own, so ``ServiceError``'s entry is met first."""
    server = _server(ServiceNotFound("nope"), exception_map={ServiceError: _handler("mine")})
    assert tool_error(await _call("wire-sync", server))["type"] == "mine"


async def test_an_exception_entry_reaches_a_project_exception() -> None:
    server = _server(TenantGone("x"), exception_map={Exception: _handler("mine")})
    assert tool_error(await _call("wire-async", server))["type"] == "mine"


@pytest.mark.parametrize(
    "denial",
    [NotPermitted, PrincipalUnavailable, type("NotYours", (NotPermitted,), {})],
    ids=["not-permitted", "principal-unavailable", "a-subclass"],
)
def test_an_entry_for_a_denial_is_refused(denial: type[Exception]) -> None:
    with pytest.raises(ImproperlyConfigured, match=f"cannot hold {denial.__name__}"):
        make_server(exception_map={denial: GONE})


async def test_a_mapped_exception_from_a_resource_read_is_not_the_maps() -> None:
    """A read's answer is contents or a JSON-RPC error, so a handler's tool result has
    nowhere to go: the read raises as it does with no map."""

    def read(*, pk: int) -> None:
        raise TenantGone("x")

    server = make_server(exception_map={TenantGone: GONE})
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=read,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=[Unrestricted()],
        ),
        name="order",
        uri_template="orders://{pk}",
    )
    with pytest.raises(TenantGone):
        handle_resources_read({"uri": "orders://1"}, call_context(server))


# ----- where a call raises it


class _RaisingCheck(PermissionCheck):
    def __init__(self) -> None:
        self.asked = 0

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.asked += 1
        raise TenantGone("from the check")


class _CountingLimit:
    def __init__(self) -> None:
        self.consumed = 0

    def consume(self, request: Any, token: Any) -> None:
        self.consumed += 1


def _check_server(**overrides: Any) -> tuple[Any, _RaisingCheck]:
    check = _RaisingCheck()
    server = make_server(**overrides)
    server.register_tool(ServiceSpec(service=lambda: None, permissions=[check]), name=TOOL)
    return server, check


@pytest.mark.parametrize("route", ROUTES)
async def test_a_check_raising_a_mapped_exception_answers_as_its_handler_says(route: str) -> None:
    """The checks are asked at admission, ahead of the catch on the wire: left to
    dispatch, as a ``ServiceError`` a check raises is, it reaches the call's arm."""
    server, _check = _check_server(exception_map={TenantGone: GONE})
    assert tool_error(await _call(route, server))["message"] == "from the check"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_check_raising_an_unmapped_exception_is_raised_as_it_is_today(route: str) -> None:
    server, check = _check_server(exception_map={KeyError: GONE})
    with pytest.raises(TenantGone):
        await _call(route, server)
    assert check.asked == 1


async def test_a_check_raising_a_mapped_exception_is_charged_like_a_service_error() -> None:
    """Left to dispatch, it is answered after the charge, as a check's ``ServiceError``."""
    limit = _CountingLimit()
    server = make_server(exception_map={TenantGone: GONE})
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=[_RaisingCheck()]),
        name=TOOL,
        rate_limits=[limit],
    )
    out = await _call("wire-sync", server)
    assert limit.consumed == 1
    assert tool_error(out)["type"] == "tenant_gone"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_seed_a_check_names_raising_a_mapped_exception_answers_as_its_handler_says(
    route: str,
) -> None:
    """A tenant seed is the likeliest place for it, and whether a check names the seed
    must not decide whether the project's handler answers."""

    def tenant() -> None:
        raise TenantGone("from the seed")

    class NamesTheSeed(PermissionCheck):
        def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
            return True

    seeds = DEFAULT_POOL_SEEDS.extend(tenant=tenant)
    server = make_server(exception_map={TenantGone: GONE}, pool_seeds=seeds)
    server.register_tool(ServiceSpec(service=lambda: None, permissions=[NamesTheSeed()]), name=TOOL)
    out = await _call(route, server, pool_seeds=seeds)
    assert tool_error(out)["message"] == "from the seed"


class _RaisingPresenter(Presenter):
    def output(self, *, call_context: dict[str, Any] | None = None) -> Any:
        return None

    def present(self, value: Any) -> Any:
        raise TenantGone("from the presenter")


def _presenter_server(**overrides: Any) -> Any:
    server = make_server(**overrides)
    server.register_tool(
        ServiceSpec(
            service=lambda: {"id": 1},
            presenter=_RaisingPresenter(),
            permissions=[Unrestricted()],
            atomic=False,
        ),
        name=TOOL,
    )
    return server


@pytest.mark.parametrize("route", ROUTES)
async def test_a_presenter_raising_a_mapped_exception_answers_as_its_handler_says(
    route: str,
) -> None:
    server = _presenter_server(exception_map={TenantGone: GONE})
    assert tool_error(await _call(route, server))["message"] == "from the presenter"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_presenter_raising_an_unmapped_exception_is_raised_as_it_is_today(
    route: str,
) -> None:
    with pytest.raises(TenantGone):
        await _call(route, _presenter_server(exception_map={KeyError: GONE}))


async def test_an_entry_for_a_presenter_s_validation_error_wins_over_the_shape_refusal() -> None:
    class Refuses(_RaisingPresenter):
        def present(self, value: Any) -> Any:
            raise ServiceValidationError("no")

    server = make_server(exception_map={ServiceValidationError: _handler("mine")})
    server.register_tool(
        ServiceSpec(
            service=lambda: {"id": 1},
            presenter=Refuses(),
            permissions=[Unrestricted()],
            atomic=False,
        ),
        name=TOOL,
    )
    assert tool_error(await _call("wire-sync", server))["type"] == "mine"


class _RaisingValidator(Validator):
    def parameters(self) -> Parameters:
        return Parameters([])

    def validate(self, arguments: Any, context: Any) -> dict[str, Any]:
        raise TenantGone("from the validator")


@pytest.mark.parametrize("route", ROUTES)
async def test_a_validator_raising_a_mapped_exception_answers_as_its_handler_says(
    route: str,
) -> None:
    server = make_server(exception_map={TenantGone: GONE})
    server.register_tool(
        ServiceSpec(
            service=lambda: None, validator=_RaisingValidator(), permissions=[Unrestricted()]
        ),
        name=TOOL,
    )
    assert tool_error(await _call(route, server))["message"] == "from the validator"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_selector_raising_a_mapped_exception_answers_as_its_handler_says(
    route: str,
) -> None:
    def find() -> None:
        raise TenantGone("from the selector")

    server = make_server(exception_map={TenantGone: GONE})
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=find, permissions=[Unrestricted()]),
        name=TOOL,
    )
    assert tool_error(await _call(route, server))["message"] == "from the selector"


# ----- what a handler is and does


async def test_a_handler_runs_off_the_event_loop_on_the_async_route() -> None:
    """A handler may read the database, as a presenter may."""
    handled_on: list[int] = []

    def handle(exc: Exception) -> ToolResult:
        handled_on.append(threading.get_ident())
        return build_error_tool_result("x", error_type="y")

    server = _server(TenantGone("x"), exception_map={TenantGone: handle})
    await _call("wire-async", server)
    assert handled_on
    assert handled_on[0] != threading.get_ident()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "returned",
    [{"isError": True}, JsonRpcError(-32603, "no"), None],
    ids=["a-dict", "a-protocol-error", "nothing"],
)
async def test_a_handler_returning_anything_else_is_a_type_error(route: str, returned: Any) -> None:
    server = _server(TenantGone("x"), exception_map={TenantGone: lambda exc: returned})
    with pytest.raises(TypeError, match="handler for TenantGone returned .* not a ToolResult"):
        await _call(route, server)


async def test_a_handler_that_raises_is_raised() -> None:
    def handle(exc: Exception) -> ToolResult:
        raise KeyError("the handler's own")

    with pytest.raises(KeyError):
        await _call("wire-sync", _server(TenantGone("x"), exception_map={TenantGone: handle}))


async def test_a_handler_may_answer_a_result_that_is_no_error() -> None:
    """Its result is served as written, so one with no ``isError`` reads as completed."""

    def handle(exc: Exception) -> ToolResult:
        return build_tool_result({"gone": True})

    out = await _call("wire-sync", _server(TenantGone("x"), exception_map={TenantGone: handle}))
    assert "isError" not in out or out["isError"] is False
    assert json.loads(out["content"][0]["text"]) == {"gone": True}


def test_the_map_is_a_copy_the_caller_cannot_edit() -> None:
    mapping: dict[type[Exception], ExceptionHandler] = {}
    server = _server(TenantGone("x"), exception_map=mapping)
    mapping[TenantGone] = GONE
    with pytest.raises(TenantGone):
        server.call_tool(TOOL, {}, user=None)


class _Span:
    def __init__(self) -> None:
        self.recorded: list[BaseException] = []

    def record_exception(self, exc: BaseException) -> None:
        self.recorded.append(exc)


@pytest.mark.parametrize("record", [True, False], ids=["recorded", "not-recorded"])
def test_a_mapped_exception_is_recorded_only_when_asked(record: bool) -> None:
    span = _Span()
    exc = TenantGone("x")
    config = replace(build_mcp_config(), record_service_exceptions=record)
    answer = answer_mapped_exception(GONE, exc, config=config, span=span)
    assert answer.is_error is True
    assert span.recorded == ([exc] if record else [])


def test_a_mapped_exception_with_no_span_is_answered_without_one() -> None:
    config = replace(build_mcp_config(), record_service_exceptions=True)
    answer = answer_mapped_exception(GONE, TenantGone("x"), config=config)
    assert answer.is_error is True


# ----- what a handler's answer announces


@pytest.mark.parametrize(
    ("handler", "announced"),
    [(GONE, 0), (lambda exc: build_tool_result({"gone": True}), 1)],
    ids=["an-error-result", "a-result-that-is-no-error"],
)
async def test_a_handler_s_answer_announces_as_its_result_reads(
    handler: Any, announced: int
) -> None:
    """``invalidates`` reads ``isError``: a handler answering with a result that is no
    error says the call completed, and its tool's resources are announced."""
    broker = InMemorySubscriptionBroker()
    server = make_server(exception_map={TenantGone: handler}, subscription_broker=broker)

    def place() -> None:
        raise TenantGone("x")

    server.register_tool(
        ServiceSpec(service=place, permissions=[Unrestricted()]),
        name=TOOL,
        invalidates=("orders://",),
    )
    queue = await broker.subscribe(frozenset({topic_for_resource("orders://")}))
    await _call("wire-async", server)
    assert queue.qsize() == announced
