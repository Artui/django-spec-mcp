from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django_service_specs import PermissionCheck, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.output import build_error_tool_result, build_tool_result
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.types.task_status import TaskStatus
from tests.tasks.conftest import RecordingExecutor, context
from tests.utils import tool_error


class TenantGone(Exception):
    pass


def _raises() -> None:
    raise TenantGone("no such tenant")


def _run(store: InMemoryTaskStore, executor: RecordingExecutor, handler: Any) -> Any:
    server = MCPServer(
        name="tasks-map",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
        exception_map={TenantGone: handler},
    )
    server.register_tool(
        ServiceSpec(service=_raises, permissions=[Unrestricted()], atomic=False),
        name="orders.place",
        description="d",
        task_policy=TaskPolicy.OPTIONAL,
    )
    handle: Any = handle_tools_call({"name": "orders.place", "arguments": {}}, context(server))
    assert handle["resultType"] == "task", handle
    server.run_task(handle["taskId"])
    return store.get(handle["taskId"])


@pytest.mark.django_db
def test_a_mapped_error_answer_is_a_completed_task_carrying_the_handler_s_result(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Completed, as a service's ``ServiceError`` is: the result says the call failed."""

    def gone(exc: Exception) -> ToolResult:
        return build_error_tool_result(str(exc), error_type="tenant_gone")

    record = _run(store, executor, gone)
    assert record.status is TaskStatus.COMPLETED, record
    assert tool_error(record.task.result) == {"type": "tenant_gone", "message": "no such tenant"}


@pytest.mark.django_db
def test_a_mapped_answer_that_is_no_error_completes_the_task(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    def fine(exc: Exception) -> ToolResult:
        return build_tool_result({"gone": True})

    record = _run(store, executor, fine)
    assert record.status is TaskStatus.COMPLETED, record
    assert record.task.result["structuredContent"] == {"gone": True}


class _RaisingCheck(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        raise TenantGone("from the check")


@pytest.mark.django_db
def test_a_check_raising_a_mapped_exception_is_answered_by_the_task_s_run(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Admission leaves it to the call, as an inline call's does."""

    def gone(exc: Exception) -> ToolResult:
        return build_error_tool_result(str(exc), error_type="tenant_gone")

    server = MCPServer(
        name="tasks-map",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
        exception_map={TenantGone: gone},
    )
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=[_RaisingCheck()], atomic=False),
        name="orders.place",
        description="d",
        task_policy=TaskPolicy.OPTIONAL,
    )
    asked = replace(context(server), exception_map=server._exception_map)
    handle: Any = handle_tools_call({"name": "orders.place", "arguments": {}}, asked)
    assert handle["resultType"] == "task", handle
    server.run_task(handle["taskId"])
    record = store.get(handle["taskId"])
    assert tool_error(record.task.result)["message"] == "from the check"
