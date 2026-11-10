from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django_service_specs import DEFAULT_POOL_SEEDS, PermissionCheck, ServiceSpec

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.types.task_status import TaskStatus
from tests.tasks.conftest import RecordingExecutor, context


class _NamesTenant(PermissionCheck):
    """Admits every caller, recording the ``tenant`` seed it names."""

    def __init__(self, checked: list[Any]) -> None:
        self.checked = checked

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        self.checked.append(tenant)
        return True


@pytest.mark.django_db
def test_a_task_run_resolves_a_checks_seed_once(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    resolved: list[object] = []
    checked: list[Any] = []
    ran: list[Any] = []

    def resolve() -> object:
        tenant = object()
        resolved.append(tenant)
        return tenant

    def whoami(*, tenant: Any) -> dict[str, str]:
        ran.append(tenant)
        return {"tenant": "seen"}

    server = MCPServer(
        name="tasks-seeds",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=resolve),
    )
    server.register_tool(
        ServiceSpec(service=whoami, permissions=[_NamesTenant(checked)], atomic=False),
        name="tenant.whoami",
        description="d",
        task_policy=TaskPolicy.OPTIONAL,
    )
    # The fixture's context leaves the server's seeds out; the worker's carries them.
    asked = replace(context(server), pool_seeds=server.pool_seeds)
    handle: Any = handle_tools_call({"name": "tenant.whoami", "arguments": {}}, asked)
    assert handle["resultType"] == "task", handle
    assert len(resolved) == 1
    resolved.clear()
    checked.clear()

    server.run_task(handle["taskId"])

    record = store.get(handle["taskId"])
    assert record.status is TaskStatus.COMPLETED, record
    assert record.task.result["structuredContent"] == {"tenant": "seen"}
    assert len(resolved) == 1, resolved
    assert checked == [resolved[0]]
    assert ran == [resolved[0]]
    assert checked[0] is ran[0]
