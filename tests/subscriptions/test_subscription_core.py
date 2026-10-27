"""The filter, the topics, the broker, and who is allowed to watch what.

Asked here of kernel specs: the resources a subscription names are kernel
``RETRIEVE`` selectors registered with ``register_resource``, and the tools that
make ``toolsListChanged`` grantable are kernel service specs registered with
``register_tool``. The filter, topic and broker questions name no spec and are
asked as the original asks them.

**Who may watch a kernel resource is who may read it**, and a kernel read is
judged by the spec's own permissions in the kernel's ``dispatch``, not by the
binding's ``permissions``, which hold only its scope gate. So the denied
caller, the deactivated one and a hand-built unguarded binding are each asked
here as well as the scope gate; the original asked only the gate a drf-services
resource carries on its binding (an ``MCPPermission`` in ``permissions=``),
which a kernel resource does not take.

The original, asking all of it of drf-services resources and tools, is kept
beside this file as ``test_subscription_core_drfs.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django_service_specs import (
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
from django_spec_mcp.handlers.types.context import MCPCallContext
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
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.tasks.conftest import RecordingExecutor

# ----- SubscriptionFilter -----


def test_only_true_opts_in() -> None:
    """Every type is opt-in and the server MUST NOT send what was not asked
    for, so anything other than an explicit ``true`` is a refusal."""
    parsed = SubscriptionFilter.from_params(
        {"toolsListChanged": True, "promptsListChanged": False, "resourcesListChanged": "yes"}
    )
    assert parsed.kinds == frozenset({NotificationKind.TOOLS_LIST_CHANGED})


def test_a_malformed_filter_is_empty_rather_than_an_error() -> None:
    assert SubscriptionFilter.from_params(None) == SubscriptionFilter()
    assert SubscriptionFilter.from_params("nope") == SubscriptionFilter()


def test_non_string_and_empty_uris_are_dropped() -> None:
    parsed = SubscriptionFilter.from_params({"resourceSubscriptions": ["a://1", 7, "", None]})
    assert parsed.resource_uris == ("a://1",)


def test_duplicate_uris_collapse_but_order_is_kept() -> None:
    parsed = SubscriptionFilter.from_params({"resourceSubscriptions": ["b://1", "a://1", "b://1"]})
    assert parsed.resource_uris == ("b://1", "a://1")


def test_task_ids_come_from_the_extensions_field() -> None:
    assert SubscriptionFilter.from_params({"taskIds": ["t1"]}).task_ids == ("t1",)


def test_the_wire_form_omits_everything_not_granted() -> None:
    """The acknowledgement reads as "these are the things you will receive", so
    a ``false`` or an empty list in it would read as a promise about
    something."""
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
    """The two are kept mechanically related so a new kind cannot be added to
    one and forgotten in the other."""
    fields = {kind.filter_field for kind in NotificationKind}
    methods = {kind.method for kind in NotificationKind}
    assert len(fields) == len(methods) == len(list(NotificationKind))
    assert all(m.startswith("notifications/") for m in methods)


# ----- topics -----


def test_topics_are_namespaced_so_one_kind_cannot_collide_with_another() -> None:
    assert topic_for_resource("x") != topic_for_kind(NotificationKind.TOOLS_LIST_CHANGED)
    assert topic_for_kind(NotificationKind.TOOLS_LIST_CHANGED).startswith("kind:")


def test_resource_topics_are_exact_not_prefixes() -> None:
    """A prefix rule would match ``invoices://1`` against ``invoices://11``,
    and would miss a tenant-scoped scheme entirely. Publishers name the
    collection explicitly instead."""
    assert topic_for_resource("invoices://1") != topic_for_resource("invoices://11")


# ----- the broker -----


async def test_a_payload_reaches_every_subscriber_of_a_topic() -> None:
    """The session broker replaces the previous subscriber; this one must not,
    or a second client watching the same resource silently disconnects the
    first."""
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
    """Topics are caller-named and unbounded, so an emptied one is removed —
    otherwise a long-lived server accumulates an entry per URI ever watched."""
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


def _context(server: MCPServer, **overrides: Any) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    request.user = AnonymousUser()
    base: dict[str, Any] = {
        "http_request": request,
        "token": TokenInfo(user=AnonymousUser()),
        "tools": server._tools,
        "resources": server._resources,
        "prompts": server._prompts,
        "protocol_version": "2026-07-28",
        "client_capabilities": {"extensions": {TASKS_EXTENSION_ID: {}}},
        "tasks": server.task_store,
        "subscriptions": server.subscription_broker,
        "config": server._config,
    }
    base.update(overrides)
    return MCPCallContext(**base)


def test_a_readable_resource_is_granted() -> None:
    server = _server()
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("open://1",)), _context(server)
    )
    assert granted.resource_uris == ("open://1",)
    assert topics == frozenset({topic_for_resource("open://1")})


def test_a_resource_the_caller_cannot_read_is_not_watchable() -> None:
    """Otherwise a subscription is a side channel around ``resources/read``:
    a caller denied the body still learns every time it changes, and *when*
    something changes is often the more sensitive signal."""
    server = _server()
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("gated://1",)), _context(server)
    )
    assert granted.resource_uris == ()
    assert topics == frozenset()


def test_an_unregistered_uri_is_refused_the_same_way_as_a_forbidden_one() -> None:
    """Indistinguishable on purpose — erroring on one and not the other would
    make the endpoint an oracle for which resources exist."""
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("nope://thing",)), _context(server)
    )
    assert granted.resource_uris == ()


def test_a_refused_entry_does_not_take_the_rest_of_the_subscription_with_it() -> None:
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("gated://1", "open://1")), _context(server)
    )
    assert granted.resource_uris == ("open://1",)


def test_a_resource_outside_the_callers_scopes_is_not_watchable() -> None:
    """The binding's own gate, judged before the spec's: a token without
    ``things:read`` is refused the read, so it is refused the watch."""
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("scoped://1",)), _context(_server())
    )
    assert granted.resource_uris == ()


def test_a_resource_inside_the_callers_scopes_is_watchable() -> None:
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("scoped://1",)),
        _context(_server(), token=TokenInfo(user=AnonymousUser(), scopes=("things:read",))),
    )
    assert granted.resource_uris == ("scoped://1",)


def test_a_deactivated_principal_cannot_watch_what_it_cannot_read() -> None:
    """The kernel refuses a deactivated principal before any permission is
    asked, with or without a grant, so the read is refused and the watch is."""
    principal = _Inactive()
    request = HttpRequest()
    request.method = "POST"
    request.user = principal
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("open://1",)),
        _context(_server(), http_request=request, token=TokenInfo(user=principal)),
    )
    assert granted.resource_uris == ()


def test_a_handbuilt_unguarded_kernel_resource_is_not_watchable() -> None:
    """A binding nobody marked unguarded is read under no grant, and the
    kernel refuses its unguarded spec, so its read never runs
    (``test_a_handbuilt_unguarded_kernel_resource_is_never_read``). The watch is
    refused the same way, as a dropped entry rather than a stream that raises."""
    server = _server()
    server.resources.register(
        KernelResourceBinding(
            name="bare",
            uri_template="bare://{pk}",
            description=None,
            selector=_thing,
            kind=SelectorKind.RETRIEVE,
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, reads=_PK),
        )
    )
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("bare://1", "open://1")), _context(server)
    )
    assert granted.resource_uris == ("open://1",)


def test_a_registered_unguarded_kernel_resource_is_watchable_with_the_setting_off() -> None:
    """The registrar marks it unguarded, the read carries a grant bound to it,
    and so the watch does: the same caller reads it and may watch it."""
    config = build_mcp_config(require_tool_permissions=False)
    server = MCPServer(name="subs", auth_backend=AllowAnyBackend(), config=config)
    with pytest.warns(UserWarning):
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_thing, reads=_PK),
            name="bare",
            uri_template="bare://{pk}",
        )
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("bare://1",)), _context(server, config=config)
    )
    assert granted.resource_uris == ("bare://1",)


def test_a_list_changed_kind_needs_a_registry_behind_it() -> None:
    """Same rule as capability advertisement: promising an event this server
    cannot produce leaves the client waiting forever."""
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(
            kinds=frozenset(
                {NotificationKind.RESOURCES_LIST_CHANGED, NotificationKind.PROMPTS_LIST_CHANGED}
            )
        ),
        _context(server),
    )
    assert granted.kinds == frozenset({NotificationKind.RESOURCES_LIST_CHANGED})


def test_tools_list_changed_is_granted_when_tools_exist() -> None:
    server = _server()
    server.register_tool(_tool(), name="t", description="x")
    granted, _ = grant_subscription(
        SubscriptionFilter(kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED})),
        _context(server),
    )
    assert granted.kinds == frozenset({NotificationKind.TOOLS_LIST_CHANGED})


def test_a_task_is_watchable_only_by_the_principal_that_created_it() -> None:
    """A task's status is as revealing as its result — knowing someone else's
    export finished is knowing they ran one."""
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
        SubscriptionFilter(task_ids=(task.task_id,)), _context(server)
    )
    theirs, _ = grant_subscription(
        SubscriptionFilter(task_ids=(task.task_id,)),
        _context(server, token=TokenInfo(user=_Other())),
    )
    assert mine.task_ids == (task.task_id,)
    assert topics == frozenset({topic_for_task(task.task_id)})
    assert theirs.task_ids == ()


def test_an_unknown_task_is_not_watchable() -> None:
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=("nope",)), _context(server))
    assert granted.task_ids == ()


def test_a_server_running_no_tasks_grants_no_task_subscriptions() -> None:
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=("t",)), _context(_server()))
    assert granted.task_ids == ()


def test_granted_and_topics_cannot_disagree() -> None:
    """They are produced together precisely so one cannot promise what the
    other does not deliver."""
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    server.register_tool(_tool(), name="t", description="x")
    granted, topics = grant_subscription(
        SubscriptionFilter(
            kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED}),
            resource_uris=("open://1", "gated://1"),
        ),
        _context(server),
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
    """Safe to call unconditionally from a service, which is what makes it
    usable as the write path's own trigger."""
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
    """No default. A quietly-constructed in-process broker would advertise
    support and then deliver nothing as soon as a second worker existed."""
    assert _server().subscription_broker is None
