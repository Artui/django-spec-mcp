from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, check_call, coerce_flat

from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    carry_token,
    principal_of,
    unguarded_grant,
)
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import check_permissions, resource_shape
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from django_spec_mcp.subscriptions.utils import (
    topic_for_kind,
    topic_for_resource,
    topic_for_task,
)
from django_spec_mcp.types.notification_kind import NotificationKind


def grant_subscription(
    requested: SubscriptionFilter, context: MCPCallContext
) -> tuple[SubscriptionFilter, frozenset[str]]:
    """Decide what a subscription may actually receive, and from which topics.

    Returns ``(granted, topics)``: ``granted`` goes back to the client in the
    acknowledgement, ``topics`` is what the stream attaches to. They are
    produced together so they cannot disagree — a topic granted but not
    announced would deliver notifications the client was told it would not get,
    and the reverse would promise silence.

    **This is the authorization boundary for the whole feature.** A topic name
    is not a secret and the broker checks nothing, so everything deciding who
    may hear what is here:

    - **A resource URI is granted only if it resolves to a registered resource
      and this caller may read it**, which for a kernel resource includes the
      spec's own permissions as well as the binding's scope gate. Otherwise a subscription is a side channel
      around ``resources/read``: a caller denied the body could still learn
      every time it changed, and activity is often the more sensitive signal.
    - **A task id is granted only to the principal that created it**, and only
      on a server that runs tasks — the comparison ``tasks/get`` makes.
    - **A list-changed kind is granted only if the server has such a registry**,
      since advertising an event this server cannot produce leaves a client
      waiting forever.

    **A refused entry is dropped, not an error.** The spec uses the
    acknowledgement as the channel for "you will not get this", omitting
    unsupported types from the agreed set. Erroring on one bad URI would also
    make the endpoint an oracle: a client could tell "no such resource" from
    "not yours" by whether the subscription opened.

    **One exception, and it is the spec's:** a client asking for ``taskIds``
    without declaring the tasks extension **MUST** get a JSON-RPC error rather
    than a quiet omission. That check runs at the transport before this — see
    ``_maybe_subscribe`` — because it produces an error response rather than a
    stream, and it is no oracle: it turns on what the *client* declared, not on
    the tasks it named.
    """
    kinds: frozenset[NotificationKind] = frozenset(
        kind for kind in requested.kinds if _has_registry(kind, context)
    )
    uris: tuple[str, ...] = tuple(
        uri for uri in requested.resource_uris if _may_watch_resource(uri, context)
    )
    task_ids: tuple[str, ...] = tuple(
        task_id for task_id in requested.task_ids if _may_watch_task(task_id, context)
    )
    granted = SubscriptionFilter(kinds=kinds, resource_uris=uris, task_ids=task_ids)
    topics: frozenset[str] = frozenset(
        [topic_for_kind(kind) for kind in kinds]
        + [topic_for_resource(uri) for uri in uris]
        + [topic_for_task(task_id) for task_id in task_ids]
    )
    return granted, topics


def _has_registry(kind: NotificationKind, context: MCPCallContext) -> bool:
    """Whether the registry behind ``kind`` holds anything.

    The spec's own example of a type to omit from the acknowledgement is
    "``promptsListChanged`` when the server has no prompts".

    Mapped explicitly, with no fallthrough: a new kind raises here rather than
    silently landing on some other registry.
    """
    registries: dict[NotificationKind, int] = {
        NotificationKind.TOOLS_LIST_CHANGED: len(context.tools),
        NotificationKind.PROMPTS_LIST_CHANGED: len(context.prompts),
        NotificationKind.RESOURCES_LIST_CHANGED: len(context.resources),
    }
    return registries[kind] > 0


def _may_watch_resource(uri: str, context: MCPCallContext) -> bool:
    """Whether this caller may be told that ``uri`` changed.

    Resolved through the same registry lookup ``resources/read`` uses, so a
    template-backed URI is matched the way the reader matches it rather than by
    a second rule that could drift.
    """
    resolved: Any = context.resources.resolve(uri)
    if resolved is None:
        return False
    binding, vars_ = resolved
    # Judged on the request the read of it is, from the same shape, so a
    # permission scoping by ``view.kwargs["project_pk"]`` grants a watch on
    # exactly the URIs it would let this caller read
    # (``test_a_subscription_is_granted_the_uris_the_read_would_admit``), and
    # one reading ``view.action`` or the query string grants what the read
    # admits (``test_a_subscription_reads_the_request_the_read_does``).
    allowed, _ = check_permissions(
        binding.permissions,
        context.http_request,
        context.token,
        shape=resource_shape(binding, vars_),
    )
    if not allowed:
        return False
    # A kernel binding's ``permissions`` are only its scope gate: the spec's own
    # permissions answer the read inside ``dispatch``, so they answer the watch
    # here too, or a caller the read refuses would still hear every change
    # (``test_a_resource_the_caller_cannot_read_is_not_watchable``).
    if runs_on_kernel(binding):
        return _kernel_read_admits(binding, vars_, context)
    return True


def _kernel_read_admits(
    binding: KernelResourceBinding, variables: dict[str, str], context: MCPCallContext
) -> bool:
    """Whether the kernel would let this caller start the read of ``binding``.

    ``check_call`` is ``dispatch``'s prelude run alone, built from what
    ``read_kernel_resource`` hands ``dispatch``: the same principal, the grant
    only a registered unguarded binding carries, the URI variables read as the
    spec's parameters, and the same call context. Each is held by a test that
    fails without it: the server's seeds by
    ``test_a_subscription_resolves_the_servers_pool_seeds``, the call context by
    ``test_a_subscription_hands_the_check_the_call_context_the_read_does`` and
    the URI variables read as the spec's types by
    ``test_a_uri_variable_is_coerced_to_the_type_the_spec_declares``.

    So a deactivated principal
    (``test_a_deactivated_principal_cannot_watch_what_it_cannot_read``) and a
    spec permission refusing the caller are refused here as the read refuses
    them. Nothing is looked up, so a check on the row itself is not asked, as
    the drf-services arm asks none either: the watch is judged on the request,
    not on a row it would have to fetch.

    Only ``DispatchError`` is a refusal. ``ImproperlyConfigured`` is how the
    kernel answers a hand-built binding over an unguarded spec, whose read
    answers -32603 and never runs: a misconfiguration, not a caller turned
    away, so it propagates and fails the watch the same way
    (``test_a_handbuilt_unguarded_kernel_resource_fails_the_watch_as_the_read_fails``)
    rather than being dropped.
    """
    principal: Any = principal_of(context.token.user)
    try:
        check_call(
            binding.spec,
            principal=principal,
            arguments=coerce_flat(binding.spec.parameters(), variables),
            grant=unguarded_grant(binding, principal, context.config),
            pool_seeds=context.pool_seeds,
            call_context=call_context(
                carry_token(context.http_request, context.token), binding.name
            ),
        )
    except DispatchError:
        return False
    return True


def _may_watch_task(task_id: str, context: MCPCallContext) -> bool:
    """Whether this caller may be told how ``task_id`` is progressing.

    The same ownership comparison ``tasks/get`` makes. A task's status is as
    revealing as its result — knowing someone else's export finished is knowing
    they ran one.
    """
    # Imported here rather than at module scope: ``handlers.tasks_utils`` reaches
    # the handler layer, which reaches back to this module through the trigger
    # path. The cycle is genuine, which is the documented exception.
    from django_spec_mcp.handlers.tasks_utils import owned_by_caller

    store = context.tasks
    if store is None:
        return False
    record = store.get(task_id)
    return record is not None and owned_by_caller(record, context)


__all__ = ["grant_subscription"]
