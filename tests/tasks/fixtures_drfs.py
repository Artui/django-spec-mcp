"""The drf-services ``server`` fixture of the tasks suite, kept until the deletion.

Every tool on it carries a ``task_policy``, which a kernel spec does not take
yet, so it has no kernel twin. ``conftest.py`` re-exports it and
``slow_service`` under the names the ``_drfs`` originals and the files beside
them were written against; the deletion takes this file and those lines
together.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework_services.types.service_spec import ServiceSpec

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore


def slow_service(**_: Any) -> dict[str, str]:
    return {"done": "yes"}


@pytest.fixture
def server(store: InMemoryTaskStore, executor: Any) -> MCPServer:
    built = MCPServer(
        name="tasks-fixture",
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
        built.register_service_tool(
            name=name,
            description="x",
            spec=ServiceSpec(service=slow_service, atomic=False),
            task_policy=policy,
        )
    return built
