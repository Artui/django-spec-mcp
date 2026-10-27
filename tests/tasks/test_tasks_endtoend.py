"""The tasks extension over the actual HTTP transport, for kernel tools.

A kernel spec takes no ``task_policy`` but the default yet (``register_tool``
refuses ``OPTIONAL`` and ``REQUIRED`` with ``ImproperlyConfigured``: *"not yet
supported for a kernel spec; such a tool runs inline"*). So what a kernel tool
can be asked here is every question that names no task policy:

- on a server that runs tasks, a tool that never opted in answers inline, to a
  client declaring the extension as to one that does not, and hands the
  executor nothing;
- ``server/discover`` advertises the extension when the server can run tasks,
  and a server with no executor, or a store with no executor, advertises
  nothing;
- an executor alone earns a store, and ``task_store=None`` is not a request for
  the default.

These name no spec at all, and are asked with a kernel tool where one is
needed to build a server.

Owed until a kernel spec takes a ``task_policy`` (each needs a tool that opted
in, so a task to follow): the task handle, polling it, the full round trip, the
non-declaring fallback, the ``REQUIRED`` denial on a server that cannot run
tasks, a legacy client, and ``test_mcp_name_must_carry_the_task_id`` and
``test_a_task_id_that_disagrees_with_the_header_is_a_mismatch``, which need a
task id to put in ``Mcp-Name``. The original is kept beside this file as
``test_tasks_endtoend_drfs.py``.
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
from django_spec_mcp.handlers.handle_initialize import build_capabilities
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.tasks.conftest import RecordingExecutor, context

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


# ----- advertisement -----


def _kernel_tool_server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="adv", auth_backend=AllowAnyBackend(), **kwargs)
    server.register_tool(
        ServiceSpec(service=_done, permissions=[Unrestricted()], atomic=False),
        name="t.inline",
        description="x",
    )
    return server


def test_discover_advertises_the_extension(client: Client) -> None:
    """Over the wire, for the module's server: the extension appears in the
    discovery document once the server can run tasks."""
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MODERN,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Method": "server/discover", "Mcp-Protocol-Version": MODERN},
    )
    result = json.loads(response.content)["result"]
    assert result["capabilities"]["extensions"] == {TASKS_EXTENSION_ID: {}}


def test_a_server_with_a_store_and_an_executor_advertises() -> None:
    """The control of the two tests below, on their own path and their own kind
    of server: with only a kernel tool registered, a server holding both halves
    of the machinery advertises, so what they find missing is the machinery and
    not an advertisement that never appears."""
    full = _kernel_tool_server(task_store=InMemoryTaskStore(), task_executor=RecordingExecutor())
    assert build_capabilities(context(full), modern=True).extensions == {TASKS_EXTENSION_ID: {}}


def test_a_server_that_runs_no_tasks_advertises_nothing() -> None:
    """A capability is a promise. Advertising one this server cannot keep would
    have a client wait for a result that never comes."""
    bare = _kernel_tool_server()
    assert build_capabilities(context(bare), modern=True).extensions is None


def test_a_store_without_an_executor_does_not_advertise() -> None:
    """Half the machinery creates tasks nothing will ever run."""
    half = _kernel_tool_server(task_store=InMemoryTaskStore())
    assert build_capabilities(context(half), modern=True).extensions is None


def test_supplying_an_executor_is_enough_to_get_a_store() -> None:
    """The executor is the switch: a store appears, namespaced like the session
    store, so the common case is one argument rather than two."""
    server = _kernel_tool_server(task_executor=RecordingExecutor())
    assert isinstance(server.task_store, DjangoCacheTaskStore)


def test_passing_task_store_none_is_not_a_request_for_the_default() -> None:
    """Distinguishable from "not passed" - a way to say "I will wire the store
    later" without silently getting one."""
    server = _kernel_tool_server(task_store=None, task_executor=RecordingExecutor())
    assert server.task_store is None
