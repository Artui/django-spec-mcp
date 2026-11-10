from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import AsyncClient, override_settings
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, NotificationKind, SubscriptionFilter
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.subscriptions.utils import (
    topic_for_kind,
    topic_for_resource,
    topic_for_task,
)
from django_spec_mcp.tasks.create_task import create_task
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import call_context

# ----- SubscriptionFilter -----


def test_only_true_opts_in() -> None:
    """Every type is opt-in: anything other than an explicit true is a refusal."""
    parsed = SubscriptionFilter.from_params(
        {"toolsListChanged": True, "promptsListChanged": False, "resourcesListChanged": "yes"}
    )
    assert parsed.kinds == frozenset({NotificationKind.TOOLS_LIST_CHANGED})


def test_a_malformed_filter_is_empty_rather_than_an_error() -> None:
    assert SubscriptionFilter.from_params(None) == SubscriptionFilter()
    assert SubscriptionFilter.from_params("nope") == SubscriptionFilter()


@pytest.mark.parametrize(
    ("uris", "expected"),
    [
        pytest.param(
            ["a://1", 7, "", None], ("a://1",), id="non-string-and-empty-uris-are-dropped"
        ),
        pytest.param(
            ["b://1", "a://1", "b://1"], ("b://1", "a://1"), id="duplicates-collapse-in-order"
        ),
    ],
)
def test_resource_uris_are_cleaned_as_they_are_parsed(
    uris: list[Any], expected: tuple[str, ...]
) -> None:
    parsed = SubscriptionFilter.from_params({"resourceSubscriptions": uris})
    assert parsed.resource_uris == expected


def test_task_ids_come_from_the_extensions_field() -> None:
    assert SubscriptionFilter.from_params({"taskIds": ["t1"]}).task_ids == ("t1",)


def test_the_wire_form_omits_everything_not_granted() -> None:
    """The acknowledgement reads as 'these are the things you will receive'."""
    assert SubscriptionFilter().to_dict() == {}


def test_the_wire_form_round_trips() -> None:
    original = SubscriptionFilter(
        kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED}),
        resource_uris=("a://1",),
        task_ids=("t1",),
    )
    assert SubscriptionFilter.from_params(original.to_dict()) == original


def test_an_empty_filter_is_falsy() -> None:
    assert not SubscriptionFilter()
    assert SubscriptionFilter(task_ids=("t",))


def test_every_kind_has_a_distinct_filter_field_and_method() -> None:
    """The two are kept mechanically related so a new kind cannot be added to one only."""
    fields = {kind.filter_field for kind in NotificationKind}
    methods = {kind.method for kind in NotificationKind}
    assert len(fields) == len(methods) == len(list(NotificationKind))
    assert all(m.startswith("notifications/") for m in methods)


# ----- topics -----


def test_topics_are_namespaced_so_one_kind_cannot_collide_with_another() -> None:
    assert topic_for_resource("x") != topic_for_kind(NotificationKind.TOOLS_LIST_CHANGED)
    assert topic_for_kind(NotificationKind.TOOLS_LIST_CHANGED).startswith("kind:")


def test_resource_topics_are_exact_not_prefixes() -> None:
    """A prefix rule would match ``invoices://1`` against ``invoices://11``."""
    assert topic_for_resource("invoices://1") != topic_for_resource("invoices://11")


# ----- the broker -----


async def test_a_payload_reaches_every_subscriber_of_a_topic() -> None:
    """The session broker replaces the previous subscriber."""
    broker = InMemorySubscriptionBroker()
    a = await broker.subscribe(frozenset({"t"}))
    b = await broker.subscribe(frozenset({"t"}))
    assert await broker.publish("t", {"n": 1}) == 2
    assert a.get_nowait() == {"n": 1}
    assert b.get_nowait() == {"n": 1}


async def test_one_subscription_watching_several_topics_reads_one_stream() -> None:
    broker = InMemorySubscriptionBroker()
    queue = await broker.subscribe(frozenset({"a", "b"}))
    await broker.publish("a", 1)
    await broker.publish("b", 2)
    assert {queue.get_nowait(), queue.get_nowait()} == {1, 2}


async def test_publishing_to_nobody_is_not_an_error() -> None:
    assert await InMemorySubscriptionBroker().publish("quiet", {}) == 0


async def test_unsubscribing_stops_delivery_and_frees_the_topic() -> None:
    """Topics are caller-named and unbounded, so an emptied one is removed."""
    broker = InMemorySubscriptionBroker()
    queue = await broker.subscribe(frozenset({"t"}))
    broker.unsubscribe(queue)
    assert await broker.publish("t", {}) == 0
    assert broker._by_topic == {}


async def test_unsubscribing_one_of_two_leaves_the_other() -> None:
    broker = InMemorySubscriptionBroker()
    a = await broker.subscribe(frozenset({"t"}))
    b = await broker.subscribe(frozenset({"t"}))
    broker.unsubscribe(a)
    assert await broker.publish("t", {}) == 1
    assert b.qsize() == 1


async def test_unsubscribing_twice_is_a_no_op() -> None:
    """The stream's ``finally`` can run after an explicit teardown."""
    broker = InMemorySubscriptionBroker()
    queue = await broker.subscribe(frozenset({"t"}))
    broker.unsubscribe(queue)
    broker.unsubscribe(queue)


async def test_a_subscription_naming_no_topics_still_gets_a_queue() -> None:
    assert isinstance(await InMemorySubscriptionBroker().subscribe(frozenset()), asyncio.Queue)


# ----- grant_subscription: the authorization boundary -----


class _Deny(PermissionCheck):
    """A spec permission refusing every principal: the kernel read's own gate."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


class _Inactive:
    """A principal the kernel refuses as deactivated, whatever its permissions."""

    pk = 7
    is_authenticated = True
    is_active = False


def _thing(*, pk: int) -> dict[str, int]:
    return {"pk": pk}


def _noop() -> dict[str, str]:
    return {"done": "yes"}


_PK = Parameters([Parameter("pk", "integer", required=True)])


def _resource(permissions: Any = None) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_thing,
        reads=_PK,
        permissions=[Unrestricted()] if permissions is None else permissions,
    )


def _tool() -> ServiceSpec:
    return ServiceSpec(service=_noop, permissions=[Unrestricted()], atomic=False)


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="subs", auth_backend=AllowAnyBackend(), **kwargs)
    server.register_resource(_resource(), name="open", uri_template="open://{pk}")
    server.register_resource(_resource([_Deny()]), name="gated", uri_template="gated://{pk}")
    server.register_resource(
        _resource(), name="scoped", uri_template="scoped://{pk}", scopes=("things:read",)
    )
    return server


async def _listen(uris: list[str]) -> Any:
    """``subscriptions/listen`` over the async mount, as a modern client posts it."""
    modern = "2026-07-28"
    return await AsyncClient().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "subscriptions/listen",
                "params": {
                    "notifications": {"resourceSubscriptions": uris},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": modern,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Method": "subscriptions/listen", "Mcp-Protocol-Version": modern},
    )


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        pytest.param(("open://1",), ("open://1",), id="a-readable-resource-is-granted"),
        # Indistinguishable from a forbidden one on purpose: erroring on one and
        pytest.param(("nope://thing",), (), id="an-unregistered-uri-is-refused"),
        pytest.param(
            ("gated://1", "open://1"),
            ("open://1",),
            id="a-refused-entry-does-not-take-the-rest-with-it",
        ),
        pytest.param(("scoped://1",), (), id="a-resource-outside-the-callers-scopes"),
        pytest.param(
            ("open://abc", "open://1"),
            ("open://1",),
            id="a-uri-whose-variable-the-spec-cannot-read-is-dropped",
        ),
    ],
)
def test_a_resource_is_watchable_exactly_when_it_is_readable(
    requested: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=requested), call_context(_server())
    )
    assert granted.resource_uris == expected
    assert topics == frozenset(topic_for_resource(uri) for uri in expected)


def test_a_resource_the_caller_cannot_read_is_not_watchable() -> None:
    """Otherwise a subscription is a side channel around ``resources/read``."""
    server = _server()
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("gated://1",)), call_context(server)
    )
    assert granted.resource_uris == ()
    assert topics == frozenset()


def test_a_resource_inside_the_callers_scopes_is_watchable() -> None:
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("scoped://1",)),
        call_context(_server(), token=TokenInfo(user=AnonymousUser(), scopes=("things:read",))),
    )
    assert granted.resource_uris == ("scoped://1",)


def test_a_deactivated_principal_cannot_watch_what_it_cannot_read() -> None:
    """The kernel refuses a deactivated principal before any permission is asked."""
    principal = _Inactive()
    request = HttpRequest()
    request.method = "POST"
    request.user = principal
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("open://1",)),
        call_context(_server(), http_request=request, token=TokenInfo(user=principal)),
    )
    assert granted.resource_uris == ()


@pytest.mark.django_db(transaction=True)
async def test_a_handbuilt_unguarded_kernel_resource_fails_the_watch_as_the_read_fails() -> None:
    """A binding nobody marked unguarded is read under no grant."""
    server = _server(subscription_broker=InMemorySubscriptionBroker())
    server.resources.register(
        KernelResourceBinding(
            name="bare",
            uri_template="bare://{pk}",
            description=None,
            selector=_thing,
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, reads=_PK),
        )
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        control = await _listen(["open://1"])
        assert control.status_code == 200
        await control.streaming_content.aclose()
        response = await _listen(["bare://1", "open://1"])
    assert response.status_code == 500
    assert json.loads(response.content)["error"]["code"] == -32603


def test_a_registered_unguarded_kernel_resource_is_watchable_with_the_setting_off() -> None:
    """The registrar marks it unguarded, so the watch carries the same grant as the read."""
    config = build_mcp_config(require_tool_permissions=False)
    server = MCPServer(name="subs", auth_backend=AllowAnyBackend(), config=config)
    with pytest.warns(UserWarning):
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, reads=_PK),
            name="bare",
            uri_template="bare://{pk}",
        )
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("bare://1",)), call_context(server, config=config)
    )
    assert granted.resource_uris == ("bare://1",)


class _OnlyTenant(PermissionCheck):
    """Admits the ``acme`` tenant, resolved from the server's own ``pool_seeds``."""

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        return tenant == "acme"


class _OnlyMarkedRequests(PermissionCheck):
    """Admits a call whose ``call_context`` carries the marked request."""

    def has_permission(self, principal: Any, spec: Any, *, call_context: dict[str, Any]) -> bool:
        return getattr(call_context["request"], "marked", False) is True


def _flag(*, v: bool) -> dict[str, bool]:
    return {"v": v}


def test_a_subscription_resolves_the_servers_pool_seeds() -> None:
    """A check naming tenant is handed what the server's seeds resolve, as the read is."""
    server = _server(
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )
    server.register_resource(
        _resource([_OnlyTenant()]), name="tenanted", uri_template="tenanted://{pk}"
    )
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("tenanted://1",)),
        call_context(server, pool_seeds=server.pool_seeds),
    )
    assert granted.resource_uris == ("tenanted://1",)
    other = _server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "globex"))
    other.register_resource(
        _resource([_OnlyTenant()]), name="tenanted", uri_template="tenanted://{pk}"
    )
    refused, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("tenanted://1",)),
        call_context(other, pool_seeds=other.pool_seeds),
    )
    assert refused.resource_uris == ()


def test_a_subscription_hands_the_check_the_call_context_the_read_does() -> None:
    """``call_context=`` of ``_kernel_read_admits``."""
    server = _server()
    server.register_resource(
        _resource([_OnlyMarkedRequests()]), name="marked", uri_template="marked://{pk}"
    )
    request = HttpRequest()
    request.method = "POST"
    request.user = None
    request.marked = True  # ty: ignore[unresolved-attribute]
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("marked://1",)),
        call_context(server, http_request=request),
    )
    assert granted.resource_uris == ("marked://1",)
    unmarked, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("marked://1",)), call_context(server)
    )
    assert unmarked.resource_uris == ()


def test_a_uri_variable_is_coerced_to_the_type_the_spec_declares() -> None:
    """``coerce_flat(...)`` of ``_kernel_read_admits``."""
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_flag,
            reads=Parameters([Parameter("v", "boolean", required=True)]),
            permissions=[Unrestricted()],
        ),
        name="flag",
        uri_template="flag://{v}",
    )
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("flag://true",)), call_context(server)
    )
    assert granted.resource_uris == ("flag://true",)


def test_a_list_changed_kind_needs_a_registry_behind_it() -> None:
    """As with capabilities: promising an event this server cannot produce strands clients."""
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(
            kinds=frozenset(
                {NotificationKind.RESOURCES_LIST_CHANGED, NotificationKind.PROMPTS_LIST_CHANGED}
            )
        ),
        call_context(server),
    )
    assert granted.kinds == frozenset({NotificationKind.RESOURCES_LIST_CHANGED})


def test_tools_list_changed_is_granted_when_tools_exist() -> None:
    server = _server()
    server.register_tool(_tool(), name="t", description="x")
    granted, _ = grant_subscription(
        SubscriptionFilter(kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED})),
        call_context(server),
    )
    assert granted.kinds == frozenset({NotificationKind.TOOLS_LIST_CHANGED})


def test_a_task_is_watchable_only_by_the_principal_that_created_it() -> None:
    """A task's status is as revealing as its result."""
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    task = create_task(
        store=store,
        executor=RecordingExecutor(store),
        tool_name="t",
        arguments={},
        token=TokenInfo(user=None),
        ttl_ms=None,
        poll_interval_ms=None,
    )

    class _Other:
        pk = 99

    mine, topics = grant_subscription(
        SubscriptionFilter(task_ids=(task.task_id,)), call_context(server)
    )
    theirs, _ = grant_subscription(
        SubscriptionFilter(task_ids=(task.task_id,)),
        call_context(server, token=TokenInfo(user=_Other())),
    )
    assert mine.task_ids == (task.task_id,)
    assert topics == frozenset({topic_for_task(task.task_id)})
    assert theirs.task_ids == ()


def test_an_unknown_task_is_not_watchable() -> None:
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=("nope",)), call_context(server))
    assert granted.task_ids == ()


def test_a_server_running_no_tasks_grants_no_task_subscriptions() -> None:
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=("t",)), call_context(_server()))
    assert granted.task_ids == ()


def test_granted_and_topics_cannot_disagree() -> None:
    """They are produced together so one cannot promise what the other does not deliver."""
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    server.register_tool(_tool(), name="t", description="x")
    granted, topics = grant_subscription(
        SubscriptionFilter(
            kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED}),
            resource_uris=("open://1", "gated://1"),
        ),
        call_context(server),
    )
    expected = {topic_for_kind(k) for k in granted.kinds} | {
        topic_for_resource(u) for u in granted.resource_uris
    }
    assert topics == expected


# ----- the publish API -----


@pytest.mark.asyncio
async def test_notify_resource_updated_reaches_a_watcher() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(subscription_broker=broker)
    queue = await broker.subscribe(frozenset({topic_for_resource("open://1")}))
    assert await server.notify_resource_updated("open://1") == 1
    frame = queue.get_nowait()
    assert frame["method"] == "notifications/resources/updated"
    assert frame["params"]["uri"] == "open://1"


@pytest.mark.asyncio
async def test_notifying_on_a_server_with_no_broker_is_a_no_op() -> None:
    """Safe to call unconditionally from a service, so usable as the write path's trigger."""
    assert await _server().notify_resource_updated("open://1") == 0


@pytest.mark.asyncio
async def test_notify_list_changed_publishes_the_kind_topic() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(subscription_broker=broker)
    queue = await broker.subscribe(
        frozenset({topic_for_kind(NotificationKind.RESOURCES_LIST_CHANGED)})
    )
    await server.notify_list_changed(NotificationKind.RESOURCES_LIST_CHANGED)
    assert queue.get_nowait()["method"] == "notifications/resources/list_changed"


def test_a_server_gets_no_broker_unless_one_is_named() -> None:
    """No default."""
    assert _server().subscription_broker is None
