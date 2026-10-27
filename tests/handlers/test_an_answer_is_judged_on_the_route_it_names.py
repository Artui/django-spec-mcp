"""A retry's answer naming another target is judged on the target it names.

A kernel service tool acting on a row names the selector that finds it, and
that selector's ``reads`` are arguments like any other, so a retry's
``inputResponses`` can name another row by answering its lookup read. The
transport merges the answers and reaches ``dispatch`` once, with the merged
arguments, so every check is asked of the row the answer names: the
class-level ``has_permission`` before anything is looked up, and
``has_object_permission`` on the row resolved. A caller granted row A who
sends A and answers B is refused, and a class-level refusal reads the same
whether B exists or not.

The drf-services original asked this of ``UrlKwarg`` route captures: a
per-binding ``DRFPermissionAdapter`` and a spec's ``permission_classes``
judged on ``view.kwargs`` before the merge and again on the route it
produced, ``same_arguments`` deciding whether it moved. None of those exists
on a kernel tool: there is no route apart from the arguments, and nothing is
judged before the merge for an answer to slip past. Its invalidation question
is asked as it was: ``invalidates=`` is rendered from the merged arguments, so
the notice names the row the service ran on. It is kept beside this file as
``test_an_answer_is_judged_on_the_route_it_names_drfs.py``.

Its charge questions hold one way only on the kernel path: a caller admitted
is charged once, while a caller refused on the row an answer names is charged
too, because the spec's checks run inside ``dispatch`` after the shell has
charged the call. The second is held as a strict ``xfail``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.subscriptions.utils import topic_for_resource
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.testapp.models import Invoice
from tests.utils import RefusingRateLimit, tool_error

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class _Note:
    note: str


class _NotBeta(PermissionCheck):
    """Admits every caller, and no row whose number starts with ``beta``."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        return not target.number.startswith("beta")


class _OnlyPk(PermissionCheck):
    """Admits only a call whose arguments name ``pk``, before anything is looked up."""

    def __init__(self, pk: int) -> None:
        self.pk = pk

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments.get("pk") == self.pk


class _CountingRateLimit:
    """An ``MCPRateLimit`` admitting every call, counting the calls it was asked about."""

    def __init__(self) -> None:
        self.consumed = 0

    def consume(self, request: Any, token: Any) -> int | None:
        self.consumed += 1
        return None


def _by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _server(
    check: PermissionCheck,
    ran: list[int],
    *,
    limiter: Any = None,
    returns_pk: bool = True,
    **registration: Any,
) -> MCPServer:
    def _annotate(*, instance: Invoice, data: _Note) -> dict[str, Any]:
        ran.append(instance.pk)
        if not returns_pk:
            return {"noted": True}
        return {"pk": instance.pk, "note": data.note}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        ServiceSpec(
            service=_annotate,
            atomic=False,
            validator=DataclassValidator(_Note),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_by_pk,
                reads=Parameters([Parameter("pk", "integer", required=True)]),
            ),
            permissions=[check],
        ),
        name="annotate",
        rate_limits=[limiter] if limiter is not None else None,
        **registration,
    )
    return server


def _ctx(server: MCPServer, subscriptions: Any = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        subscriptions=subscriptions,
    )


async def _answer(
    server: MCPServer,
    sent: dict[str, Any],
    answer: dict[str, Any],
    *,
    is_async: bool,
    subscriptions: Any = None,
) -> Any:
    params: dict[str, Any] = {
        "name": "annotate",
        "arguments": sent,
        "inputResponses": {ELICITATION_KEY: {"action": "accept", "content": answer}},
    }
    if is_async:
        return await handle_tools_call_async(params, _ctx(server, subscriptions))
    return await sync_to_async(handle_tools_call)(params, _ctx(server, subscriptions))


def _forbidden(out: Any) -> bool:
    return isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.fixture
async def rows() -> tuple[Invoice, Invoice]:
    acme = await Invoice.objects.acreate(number="acme-1")
    beta = await Invoice.objects.acreate(number="beta-1")
    return acme, beta


_ASYNC = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


@_ASYNC
async def test_an_answer_naming_a_row_the_caller_may_not_touch_is_refused(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    acme, beta = rows
    ran: list[int] = []

    out = await _answer(
        _server(_NotBeta(), ran), {"pk": acme.pk, "note": "x"}, {"pk": beta.pk}, is_async=is_async
    )

    assert _forbidden(out), out
    assert ran == []


@_ASYNC
async def test_an_answer_naming_a_row_the_caller_may_touch_runs_on_it(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    # The other half: answered over the refused row, the admitted one is the
    # one the service runs on.
    acme, beta = rows
    ran: list[int] = []

    out = await _answer(
        _server(_NotBeta(), ran), {"pk": beta.pk, "note": "x"}, {"pk": acme.pk}, is_async=is_async
    )

    assert out["structuredContent"] == {"pk": acme.pk, "note": "x"}
    assert ran == [acme.pk]


@_ASYNC
async def test_an_answer_cannot_tell_an_existing_target_from_a_missing_one(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    # The class-level check is asked before the row is looked up, so a caller
    # it refuses learns nothing of which rows exist.
    acme, beta = rows
    ran: list[int] = []
    server = _server(_OnlyPk(acme.pk), ran)
    sent = {"pk": acme.pk, "note": "x"}

    existing = await _answer(server, sent, {"pk": beta.pk}, is_async=is_async)
    missing = await _answer(server, sent, {"pk": beta.pk + 1000}, is_async=is_async)

    assert _forbidden(existing), existing
    assert _forbidden(missing), missing
    assert ran == []


@_ASYNC
async def test_an_answer_filling_the_lookup_left_out_is_judged_on_the_row_it_names(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    acme, beta = rows
    ran: list[int] = []
    server = _server(_NotBeta(), ran)

    refused = await _answer(server, {"note": "x"}, {"pk": beta.pk}, is_async=is_async)
    admitted = await _answer(server, {"note": "x"}, {"pk": acme.pk}, is_async=is_async)

    assert _forbidden(refused), refused
    assert admitted["structuredContent"] == {"pk": acme.pk, "note": "x"}
    assert ran == [acme.pk]


@_ASYNC
async def test_an_answer_naming_no_row_is_not_found(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    # Past the class-level check, a row-level one has no row to judge, so the
    # answer is the missing row's; ``_OnlyPk`` above is what keeps a caller
    # the spec refuses from learning it.
    acme, _ = rows
    ran: list[int] = []

    out = await _answer(
        _server(_NotBeta(), ran), {"pk": acme.pk, "note": "x"}, {"pk": 0}, is_async=is_async
    )

    assert tool_error(out)["type"] == "not_found"
    assert ran == []


@_ASYNC
async def test_the_invalidation_names_the_row_an_answer_moved_to(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    # The result carries no ``pk``, so the template is rendered from the
    # arguments, and it must be the merged ones: from the arguments as sent, a
    # subscriber to the row the service ran on misses the change.
    acme, beta = rows
    broker = InMemorySubscriptionBroker()
    moved_to = await broker.subscribe(frozenset({topic_for_resource(f"invoices://{acme.pk}")}))
    sent_to = await broker.subscribe(frozenset({topic_for_resource(f"invoices://{beta.pk}")}))
    ran: list[int] = []
    server = _server(_NotBeta(), ran, returns_pk=False, invalidates=("invoices://{pk}",))

    out = await _answer(
        server,
        {"pk": beta.pk, "note": "x"},
        {"pk": acme.pk},
        is_async=is_async,
        subscriptions=broker,
    )

    assert "isError" not in out, out
    assert ran == [acme.pk]
    assert moved_to.get_nowait()["params"]["uri"] == f"invoices://{acme.pk}"
    assert sent_to.qsize() == 0


@_ASYNC
async def test_a_caller_admitted_on_the_answered_row_is_charged_once(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    acme, beta = rows
    limit = _CountingRateLimit()
    ran: list[int] = []

    out = await _answer(
        _server(_NotBeta(), ran, limiter=limit),
        {"pk": beta.pk, "note": "x"},
        {"pk": acme.pk},
        is_async=is_async,
    )

    assert "isError" not in out, out
    assert limit.consumed == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A kernel spec's checks run inside dispatch, after handle_tools_call*.py has charged "
        "the rate limit, so a caller they refuse is charged and, against a spent quota, told "
        "RATE_LIMITED. Fixed by authorizing a kernel spec in the shell, on the merged "
        "arguments and before consume_rate_limits, and handing dispatch the grant."
    ),
)
@_ASYNC
async def test_a_caller_refused_on_the_row_an_answer_names_is_not_charged(
    rows: tuple[Invoice, Invoice], is_async: bool
) -> None:
    acme, beta = rows
    limit = RefusingRateLimit()
    ran: list[int] = []

    out = await _answer(
        _server(_OnlyPk(acme.pk), ran, limiter=limit),
        {"pk": acme.pk, "note": "x"},
        {"pk": beta.pk},
        is_async=is_async,
    )

    assert _forbidden(out), out
    assert limit.consumed == 0
