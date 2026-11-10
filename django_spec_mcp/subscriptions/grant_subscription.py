from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, check_call, coerce_flat

from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    carry_token,
    principal_of,
    unguarded_grant,
)
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
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
    """Decide what a subscription may actually receive, and from which topics."""
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
    """Whether the registry behind ``kind`` holds anything."""
    registries: dict[NotificationKind, int] = {
        NotificationKind.TOOLS_LIST_CHANGED: len(context.tools),
        NotificationKind.PROMPTS_LIST_CHANGED: len(context.prompts),
        NotificationKind.RESOURCES_LIST_CHANGED: len(context.resources),
    }
    return registries[kind] > 0


def _may_watch_resource(uri: str, context: MCPCallContext) -> bool:
    """Whether this caller may be told that ``uri`` changed."""
    try:
        resolved: Any = context.resources.resolve(uri)
    except ValueError:
        return False
    if resolved is None:
        return False
    binding, vars_ = resolved
    allowed, _ = judge_binding(binding, context.token)
    if not allowed:
        return False
    if runs_on_kernel(binding):
        return _kernel_read_admits(binding, vars_, context)
    return True


def _kernel_read_admits(
    binding: KernelResourceBinding, variables: dict[str, str], context: MCPCallContext
) -> bool:
    """Whether the kernel would let this caller start the read of ``binding``."""
    principal: Any = principal_of(context.token.user)
    asked = call_context(carry_token(context.http_request, context.token), binding.name)
    try:
        check_call(
            binding.spec,
            principal=principal,
            arguments=coerce_flat(binding.spec.parameters(call_context=asked), variables),
            grant=unguarded_grant(binding, principal, context.config),
            pool_seeds=context.pool_seeds,
            call_context=asked,
        )
    except DispatchError:
        return False
    return True


def _may_watch_task(task_id: str, context: MCPCallContext) -> bool:
    """Whether this caller may be told how ``task_id`` is progressing."""
    from django_spec_mcp.handlers.tasks_utils import owned_by_caller

    store = context.tasks
    if store is None:
        return False
    record = store.get(task_id)
    return record is not None and owned_by_caller(record, context)


__all__ = ["grant_subscription"]
