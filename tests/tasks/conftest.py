"""Shared fixtures for the tasks suite.

The executors here are the point of the seam: neither imports a queue, and the
package never learns which one it got.

``policy_server`` is the kernel twin of the drf-services ``server`` fixture in
``fixtures_drfs.py``: one kernel tool per ``TaskPolicy``. Nothing here imports
that module, so deleting the ``*_drfs.py`` files leaves this conftest
collectable; a test using it imports it from that module itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID


class RecordingExecutor:
    """Records what it was handed instead of queueing it.

    Stands in for the Celery call. The only thing a test needs to assert about
    the hand-off is *that* it happened, with which id, and when relative to the
    store write.
    """

    def __init__(self, store: Any = None) -> None:
        self.enqueued: list[str] = []
        # When set, every ``enqueue`` reads the store back — which is how the
        # "durable before hand-off" requirement is actually observable.
        self._store = store
        self.seen_in_store: list[bool] = []

    def enqueue(self, task_id: str) -> None:
        self.enqueued.append(task_id)
        if self._store is not None:
            self.seen_in_store.append(self._store.get(task_id) is not None)


class BrokenExecutor:
    """An executor whose broker is down."""

    def enqueue(self, task_id: str) -> None:
        raise RuntimeError("broker unreachable")


@pytest.fixture
def store() -> InMemoryTaskStore:
    return InMemoryTaskStore()


@pytest.fixture
def executor(store: InMemoryTaskStore) -> RecordingExecutor:
    return RecordingExecutor(store)


@pytest.fixture
def server(store: InMemoryTaskStore, executor: RecordingExecutor) -> MCPServer:
    """A task-running server with no tool: a task created by name, or run when it
    names no live tool, asks nothing of a spec.

    The drf-services server, whose tools carry a ``task_policy``, is
    ``fixtures_drfs``' ``server``; a test importing it overrides this one.
    """
    return MCPServer(
        name="tasks-kernel",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
    )


def done() -> dict[str, str]:
    return {"done": "yes"}


def register_kernel_tool(server: MCPServer, name: str, service: Any, **kwargs: Any) -> None:
    """Register ``service`` as an open kernel service tool, with ``kwargs`` as its knobs."""
    server.register_tool(
        ServiceSpec(service=service, permissions=[Unrestricted()], atomic=False),
        name=name,
        description="x",
        **kwargs,
    )


@pytest.fixture
def policy_server(store: InMemoryTaskStore, executor: RecordingExecutor) -> MCPServer:
    """A task-running server with one kernel tool per policy, each answering ``done``."""
    built = MCPServer(
        name="tasks-policies",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        task_store=store,
        task_executor=executor,
    )
    for name, policy in (
        ("tasks.optional", TaskPolicy.OPTIONAL),
        ("tasks.required", TaskPolicy.REQUIRED),
        ("tasks.inline", TaskPolicy.FORBIDDEN),
    ):
        register_kernel_tool(built, name, done, task_policy=policy)
    return built


def context(
    server: MCPServer,
    *,
    declares: bool = True,
    user: Any = None,
    scopes: tuple[str, ...] = (),
) -> MCPCallContext:
    """A request context, with or without the tasks extension declared."""
    from django.http import HttpRequest

    http_request = HttpRequest()
    http_request.method = "POST"
    http_request.user = user
    return MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=user, scopes=scopes),
        tools=server._tools,
        resources=server._resources,
        prompts=server._prompts,
        protocol_version="2026-07-28",
        client_capabilities=(
            {"extensions": {TASKS_EXTENSION_ID: {}}} if declares else {"extensions": {}}
        ),
        tasks=server.task_store,
        task_executor=server.task_executor,
        config=server._config,
    )


__all__ = [
    "BrokenExecutor",
    "RecordingExecutor",
    "context",
    "done",
    "executor",
    "policy_server",
    "register_kernel_tool",
    "server",
    "store",
]
