"""The drf-services ``server`` fixture of the tasks suite, kept until the deletion.

Its kernel twin is ``policy_server`` in ``conftest.py``. A test using it, or
``slow_service``, imports it from here; ``conftest.py`` imports nothing from
this module, so the deletion takes this file and leaves the conftest
collectable.
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
