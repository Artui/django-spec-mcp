from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django_service_specs import (
    AdditionalInputRequired,
    DataclassValidator,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.subscriptions.render_invalidations import render_invalidations
from django_spec_mcp.subscriptions.utils import topic_for_resource
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import call_context

# ----- rendering -----


@pytest.mark.parametrize(
    ("templates", "payload", "arguments", "expected"),
    [
        pytest.param(
            ("invoices://{pk}",),
            {"structuredContent": {"pk": 7}},
            {},
            ("invoices://7",),
            id="a-placeholder-is-filled-from-the-result",
        ),
        # The delete case: the call returns nothing and ``{pk}`` lives only in
        pytest.param(
            ("invoices://{pk}",),
            {"structuredContent": {}},
            {"pk": 3},
            ("invoices://3",),
            id="the-arguments-fill-what-the-result-lacks",
        ),
        pytest.param(
            ("invoices://{pk}",),
            {"structuredContent": {"pk": "new"}},
            {"pk": "old"},
            ("invoices://new",),
            id="the-result-wins-over-the-arguments",
        ),
        pytest.param(
            ("invoices://",), {}, {}, ("invoices://",), id="no-placeholders-is-the-static-case"
        ),
        pytest.param(
            ("invoices://{nope}",), {}, {}, (), id="an-unfillable-template-is-dropped-not-raised"
        ),
        pytest.param(
            ("invoices://{pk}",),
            {"structuredContent": {"pk": None}},
            {},
            (),
            id="a-null-value-is-as-unusable-as-a-missing-one",
        ),
        pytest.param(
            ("invoices://{nope}", "invoices://"),
            {},
            {},
            ("invoices://",),
            id="one-unfillable-template-does-not-take-the-others-with-it",
        ),
        pytest.param(
            ("b://{pk}", "a://", "b://{pk}"),
            {"structuredContent": {"pk": 1}},
            {},
            ("b://1", "a://"),
            id="duplicates-collapse-and-order-is-kept",
        ),
        pytest.param(
            ("invoices://{pk}",),
            {"structuredContent": {"items": [{"pk": 1}]}},
            {},
            (),
            id="a-list-payload-contributes-no-keys",
        ),
        pytest.param(("a://{pk}",), "text", {}, (), id="a-non-dict-result-contributes-no-keys"),
    ],
)
def test_render_invalidations(
    templates: tuple[str, ...], payload: Any, arguments: dict[str, Any], expected: tuple[str, ...]
) -> None:
    assert render_invalidations(templates, payload=payload, arguments=arguments) == expected


# ----- dispatch -----


@dataclass
class _PkInput:
    pk: int = 1


def _service(*, pk: int = 1) -> dict[str, Any]:
    return {"pk": pk}


def _failing(*, pk: int = 1) -> Any:  # noqa: ARG001
    raise ServiceError("nope")


def _spec(service: Any, validator: type = _PkInput, **kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("permissions", [Unrestricted()])
    return ServiceSpec(
        service=service, validator=DataclassValidator(validator), atomic=False, **kwargs
    )


def _server(broker: Any) -> MCPServer:
    server = MCPServer(name="inval", auth_backend=AllowAnyBackend(), subscription_broker=broker)
    server.register_tool(
        _spec(_service),
        name="invoices.create",
        description="x",
        invalidates=("invoices://{pk}", "invoices://"),
    )
    server.register_tool(
        _spec(_failing), name="invoices.broken", description="x", invalidates=("invoices://{pk}",)
    )
    server.register_tool(_spec(_service), name="invoices.quiet", description="x")
    return server


@pytest.mark.django_db(transaction=True)
async def test_a_successful_call_announces_both_declared_uris() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    instance = await broker.subscribe(frozenset({topic_for_resource("invoices://1")}))
    collection = await broker.subscribe(frozenset({topic_for_resource("invoices://")}))

    await handle_tools_call_async(
        {"name": "invoices.create", "arguments": {}}, call_context(server)
    )
    assert instance.get_nowait()["params"]["uri"] == "invoices://1"
    assert collection.get_nowait()["params"]["uri"] == "invoices://"


@pytest.mark.django_db(transaction=True)
async def test_a_failed_tool_announces_nothing() -> None:
    """Checked on ``isError``, not on the result being present."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = await broker.subscribe(frozenset({topic_for_resource("invoices://1")}))
    result = await handle_tools_call_async(
        {"name": "invoices.broken", "arguments": {}}, call_context(server)
    )
    assert result["isError"] is True
    assert queue.qsize() == 0


def _confirming(*, confirmed: bool = False) -> dict[str, Any]:
    """Runs only once the caller has confirmed; asks for the confirmation otherwise."""
    if not confirmed:
        raise AdditionalInputRequired(
            "Confirm to proceed.", schema={"confirmed": {"type": "boolean"}}
        )
    return {"pk": 1}


@dataclass
class _ConfirmInput:
    confirmed: bool = False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    ("arguments", "announced"),
    [({}, 0), ({"confirmed": True}, 1)],
    ids=["input-required", "completed"],
)
async def test_only_a_completed_result_announces(
    is_async: bool, arguments: dict[str, Any], announced: int
) -> None:
    """A result asking for more input ran nothing, so nothing changed."""
    broker = InMemorySubscriptionBroker()
    server = MCPServer(name="inval", auth_backend=AllowAnyBackend(), subscription_broker=broker)
    server.register_tool(
        _spec(_confirming, _ConfirmInput),
        name="invoices.confirm",
        description="x",
        invalidates=("invoices://",),
    )
    queue = await broker.subscribe(frozenset({topic_for_resource("invoices://")}))
    context = call_context(server, client_capabilities={"elicitation": {}})
    params: dict[str, Any] = {"name": "invoices.confirm", "arguments": arguments}

    if is_async:
        result: Any = await handle_tools_call_async(params, context)
    else:
        result = await sync_to_async(handle_tools_call)(params, context)

    assert result.get("resultType") == ("input_required" if not announced else None), result
    assert queue.qsize() == announced


class _Refuses(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


@pytest.mark.django_db(transaction=True)
async def test_a_non_dict_result_announces_nothing() -> None:
    """A denied call is answered with a protocol error rather than a result."""
    broker = InMemorySubscriptionBroker()
    server = MCPServer(name="inval", auth_backend=AllowAnyBackend(), subscription_broker=broker)
    server.register_tool(
        _spec(_service, permissions=[_Refuses()]),
        name="invoices.denied",
        description="x",
        invalidates=("invoices://",),
    )
    queue = await broker.subscribe(frozenset({topic_for_resource("invoices://")}))

    result: Any = await handle_tools_call_async(
        {"name": "invoices.denied", "arguments": {}}, call_context(server)
    )

    assert result.code == -32006
    assert queue.qsize() == 0


@pytest.mark.django_db(transaction=True)
async def test_a_binding_that_declares_nothing_publishes_nothing() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = await broker.subscribe(frozenset({topic_for_resource("invoices://1")}))
    await handle_tools_call_async({"name": "invoices.quiet", "arguments": {}}, call_context(server))
    assert queue.qsize() == 0


@pytest.mark.django_db(transaction=True)
async def test_an_unknown_tool_does_not_reach_the_publisher() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    result = await handle_tools_call_async({"name": "nope", "arguments": {}}, call_context(server))
    assert result.code == -32602


@pytest.mark.django_db(transaction=True)
def test_the_sync_path_announces_too() -> None:
    """The two ``tools/call`` handlers are parallel implementations."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    from asgiref.sync import async_to_sync

    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))
    handle_tools_call({"name": "invoices.create", "arguments": {}}, call_context(server))
    assert queue.qsize() == 1


@pytest.mark.django_db(transaction=True)
def test_nothing_is_published_until_the_transaction_commits() -> None:
    """Otherwise a subscriber re-reads on the notice and sees the old value."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    from asgiref.sync import async_to_sync

    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))
    with transaction.atomic():
        handle_tools_call({"name": "invoices.create", "arguments": {}}, call_context(server))
        assert queue.qsize() == 0, "published before commit"
    assert queue.qsize() == 1


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_write_announces_nothing() -> None:
    """The other half of the same rule, and the one that would actually lie."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    from asgiref.sync import async_to_sync

    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))

    class _Rollback(Exception):
        pass

    with pytest.raises(_Rollback), transaction.atomic():
        handle_tools_call({"name": "invoices.create", "arguments": {}}, call_context(server))
        raise _Rollback
    assert queue.qsize() == 0


@pytest.mark.django_db(transaction=True)
def test_the_async_path_announces_on_the_thread_that_did_the_write() -> None:
    """The bug this pins."""
    from asgiref.sync import async_to_sync

    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))
    with transaction.atomic():
        async_to_sync(handle_tools_call_async)(
            {"name": "invoices.create", "arguments": {}}, call_context(server)
        )
        assert queue.qsize() == 0, "announced before commit"
    assert queue.qsize() == 1


@pytest.mark.django_db(transaction=True)
async def test_publishing_nothing_is_a_no_op_on_both_paths() -> None:
    from django_spec_mcp.subscriptions.publish_invalidations import publish_invalidations

    broker = InMemorySubscriptionBroker()
    publish_invalidations(broker, ())
    publish_invalidations(None, ("a://1",))


@pytest.mark.django_db(transaction=True)
async def test_a_server_with_no_broker_is_unaffected() -> None:
    """``invalidates=`` on a server that pushes nothing is inert, not an error."""
    server = MCPServer(name="nobroker", auth_backend=AllowAnyBackend())
    server.register_tool(
        _spec(_service), name="invoices.create", description="x", invalidates=("invoices://{pk}",)
    )
    result = await handle_tools_call_async(
        {"name": "invoices.create", "arguments": {}}, call_context(server)
    )
    assert result["structuredContent"] == {"pk": 1}


# ----- registration surface -----


def test_a_selector_tool_refuses_invalidates() -> None:
    """A read changes nothing, and accepting the knob would invite using it."""
    server = MCPServer(name="sel", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="invalidates belongs to a service tool"):
        server.register_tool(
            SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [], permissions=[Unrestricted()]),
            name="s",
            description="x",
            paginate=True,
            invalidates=("a://",),
        )


@pytest.mark.django_db(transaction=True)
def test_the_wsgi_transport_hands_the_handler_a_broker() -> None:
    """The announcement is dead on WSGI unless the *transport* passes the broker."""
    from asgiref.sync import async_to_sync
    from django.test import RequestFactory

    from django_spec_mcp.transport.streamable_http_view import (
        StreamableHttpView,
    )

    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))

    view = StreamableHttpView.as_view(
        tools=server._tools,
        resources=server._resources,
        prompts=server._prompts,
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        subscription_broker=broker,
        config=build_mcp_config(allowed_origins=["*"], sessions_enabled=False),
    )
    request = RequestFactory().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "invoices.create", "arguments": {}},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25"},
    )
    response = view(request)
    assert response.status_code == 200
    assert queue.qsize() == 1
    assert queue.get_nowait()["params"]["uri"] == "invoices://"


@pytest.mark.django_db(transaction=True)
def test_the_mounted_wsgi_urls_hand_the_handler_a_broker() -> None:
    """The same guarantee, reached the way a project actually mounts the server."""
    import json as _json

    from asgiref.sync import async_to_sync
    from django.test import RequestFactory

    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    server._config = build_mcp_config(allowed_origins=["*"], sessions_enabled=False)
    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))

    patterns, _app_name, _namespace = server.urls
    view = next(p.callback for p in patterns if p.callback is not None)

    request = RequestFactory().post(
        "/mcp/",
        data=_json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "invoices.create", "arguments": {}},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25"},
    )
    response = view(request)
    assert response.status_code == 200
    assert queue.qsize() == 1
    assert queue.get_nowait()["params"]["uri"] == "invoices://"
