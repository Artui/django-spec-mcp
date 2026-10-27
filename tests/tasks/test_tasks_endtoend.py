"""The tasks extension over the actual HTTP transport, for a kernel tool.

A kernel spec takes no ``task_policy`` but the default yet (``register_tool``
refuses ``OPTIONAL`` and ``REQUIRED`` with ``ImproperlyConfigured``: *"not yet
supported for a kernel spec; such a tool runs inline"*). So the one question of
the original that a kernel tool can be asked is the ``FORBIDDEN`` one: on a
server that runs tasks, a tool that never opted in answers inline, to a client
declaring the extension as to one that does not, and hands the executor
nothing.

Every other question of the original needs a tool that opted in (the task
handle, polling it, the full round trip, the non-declaring fallback, the
``REQUIRED`` denial, a legacy client, a server that cannot run tasks) and is
owed until a kernel spec takes a ``task_policy``. The original is kept beside
this file as ``test_tasks_endtoend_drfs.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import path
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.tasks.conftest import RecordingExecutor

MODERN = "2026-07-28"

STORE = InMemoryTaskStore()
EXECUTOR = RecordingExecutor(STORE)


def _done() -> dict[str, str]:
    return {"done": "yes"}


def _build() -> MCPServer:
    server = MCPServer(
        name="tasks-e2e-kernel",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        task_store=STORE,
        task_executor=EXECUTOR,
    )
    server.register_tool(
        ServiceSpec(service=_done, permissions=[Unrestricted()], atomic=False),
        name="t.inline",
        description="x",
    )
    return server


SERVER = _build()

urlpatterns: list[Any] = [path("mcp/", SERVER.urls)]

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.urls(__name__)]


def _call(client: Client, *, declares: bool) -> Any:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {
            "extensions": {TASKS_EXTENSION_ID: {}} if declares else {}
        },
    }
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "t.inline", "arguments": {}, "_meta": meta},
            }
        ),
        content_type="application/json",
        headers={
            "Mcp-Method": "tools/call",
            "Mcp-Protocol-Version": MODERN,
            "Mcp-Name": "t.inline",
        },
    )
    return json.loads(response.content)["result"]


@pytest.mark.parametrize("declares", [True, False], ids=["declaring", "not-declaring"])
def test_a_tool_that_never_opted_in_is_unaffected(client: Client, declares: bool) -> None:
    before = list(EXECUTOR.enqueued)
    result = _call(client, declares=declares)
    assert result["structuredContent"] == {"done": "yes"}
    # A completed result, not a task handle: the modern transport names which.
    assert result["resultType"] == "complete"
    assert EXECUTOR.enqueued == before
