"""A kernel tool's task is judged, and recorded, with the server's value in place.

The binding's ``server_arguments`` are applied before the task is created, so
the permission check at its creation sees the bound value, and the task stores
the arguments the tool will run with: the bound value over the caller's, and
the withheld name dropped. Applied after, the check would see the caller's
value and the task record it, while the worker, which applies the binding
again, still ran with the bound one. Held for both handlers, since each orders
the two steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor, context


@dataclass
class _Card:
    project: int
    draw: int


def _card(*, project: int, draw: int = 0) -> _Card:
    return _Card(project=project, draw=draw)


@pytest.mark.django_db
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_kernel_task_is_recorded_and_run_with_the_bound_value(is_async: bool) -> None:
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), task_store=store, task_executor=executor
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_card,
            reads=Parameters(
                [
                    Parameter("project", "integer", required=True),
                    Parameter("draw", "integer", required=False),
                ]
            ),
            presenter=DataclassPresenter(_Card),
            permissions=[Unrestricted()],
        ),
        name="card",
        description="d",
        server_arguments=ServerArguments(values={"project": 7}, withheld=frozenset({"draw"})),
        task_policy=TaskPolicy.REQUIRED,
    )
    params = {"name": "card", "arguments": {"project": 99, "draw": 3}}
    out: Any = (
        await handle_tools_call_async(params, context(server))
        if is_async
        else handle_tools_call(params, context(server))
    )
    assert out["resultType"] == "task", out
    task_id = executor.enqueued[0]
    assert store.get(task_id).arguments == {"project": 7}
    await sync_to_async(server.run_task)(task_id)
    assert store.get(task_id).task.result["structuredContent"] == {"project": 7, "draw": 0}
