from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import path
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_initialize import build_capabilities
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.tasks.conftest import RecordingExecutor, context

MODERN = "2026-07-28"
LEGACY = "2025-11-25"

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
    for name, policy in (
        ("t.optional", TaskPolicy.OPTIONAL),
        ("t.required", TaskPolicy.REQUIRED),
        ("t.inline", TaskPolicy.FORBIDDEN),
    ):
        server.register_tool(
            ServiceSpec(service=_done, permissions=[Unrestricted()], atomic=False),
            name=name,
            description="x",
            task_policy=policy,
        )
    return server


SERVER = _build()

urlpatterns: list[Any] = [path("mcp/", SERVER.urls)]

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.urls(__name__)]


def _post(
    client: Client,
    method: str,
    params: dict[str, Any],
    *,
    declares: bool = True,
    headers: dict[str, str] | None = None,
) -> Any:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {
            "extensions": {TASKS_EXTENSION_ID: {}} if declares else {}
        },
    }
    sent: dict[str, str] = {"Mcp-Method": method, "Mcp-Protocol-Version": MODERN}
    name = params.get("name") or params.get("taskId")
    if name is not None:
        sent["Mcp-Name"] = name
    sent.update(headers or {})
    return client.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}}
        ),
        content_type="application/json",
        headers=sent,
    )


def _result(response: Any) -> Any:
    return json.loads(response.content)["result"]


def _error(response: Any) -> Any:
    return json.loads(response.content)["error"]


def _call(client: Client, *, declares: bool, name: str = "t.inline") -> Any:
    return _result(_post(client, "tools/call", {"name": name, "arguments": {}}, declares=declares))


# ----- the decision at tools/call -----


def test_a_declaring_client_gets_a_task_handle(client: Client) -> None:
    result = _call(client, declares=True, name="t.optional")
    assert result["resultType"] == "task"
    assert result["status"] == "working"
    assert result["taskId"] in EXECUTOR.enqueued


def test_the_handle_resolves_immediately_by_polling(client: Client) -> None:
    """The durability requirement, seen from outside."""
    task_id = _call(client, declares=True, name="t.optional")["taskId"]
    assert _result(_post(client, "tasks/get", {"taskId": task_id}))["status"] == "working"


def test_the_full_round_trip(client: Client) -> None:
    task_id = _call(client, declares=True, name="t.optional")["taskId"]
    SERVER.run_task(task_id)
    polled = _result(_post(client, "tasks/get", {"taskId": task_id}))
    assert polled["status"] == "completed"
    assert polled["result"]["structuredContent"] == {"done": "yes"}


def test_a_non_declaring_client_gets_the_result_inline_when_the_policy_allows(
    client: Client,
) -> None:
    """``OPTIONAL`` means a task *if it can*, and an ordinary call otherwise."""
    result = _call(client, declares=False, name="t.optional")
    assert result["structuredContent"] == {"done": "yes"}
    assert "taskId" not in result


def test_a_non_declaring_client_calling_a_task_only_tool_is_refused(client: Client) -> None:
    """The case the spec names: unable to service the request without a task handle."""
    response = _post(client, "tools/call", {"name": "t.required", "arguments": {}}, declares=False)
    assert response.status_code == 400
    assert _error(response)["code"] == -32021


def test_a_legacy_client_never_gets_a_task(client: Client) -> None:
    """Not an era branch — it falls out of the shape."""
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": LEGACY, "capabilities": {}},
            }
        ),
        content_type="application/json",
    )
    session = response.headers["Mcp-Session-Id"]
    before = list(EXECUTOR.enqueued)
    called = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "t.optional", "arguments": {}},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Session-Id": session, "Mcp-Protocol-Version": LEGACY},
    )
    assert _result(called)["structuredContent"] == {"done": "yes"}
    assert EXECUTOR.enqueued == before


# ----- routing headers -----


def test_mcp_name_must_carry_the_task_id(client: Client) -> None:
    """The extension requires it so a gateway can route a follow-up to the holding instance."""
    task_id = _call(client, declares=True, name="t.optional")["taskId"]
    assert _post(client, "tasks/get", {"taskId": task_id}).status_code == 200


def test_a_task_id_that_disagrees_with_the_header_is_a_mismatch(client: Client) -> None:
    task_id = _call(client, declares=True, name="t.optional")["taskId"]
    response = _post(
        client, "tasks/get", {"taskId": task_id}, headers={"Mcp-Name": "something-else"}
    )
    assert response.status_code == 400
    assert _error(response)["code"] == -32020


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
    """Over the wire: the extension appears in discovery once the server can run tasks."""
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
    """The control of the two tests below, on their own path and their own kind of server."""
    full = _kernel_tool_server(task_store=InMemoryTaskStore(), task_executor=RecordingExecutor())
    assert build_capabilities(context(full), modern=True).extensions == {TASKS_EXTENSION_ID: {}}


def test_a_server_that_runs_no_tasks_advertises_nothing() -> None:
    """A capability is a promise."""
    bare = _kernel_tool_server()
    assert build_capabilities(context(bare), modern=True).extensions is None


def test_a_store_without_an_executor_does_not_advertise() -> None:
    """Half the machinery creates tasks nothing will ever run."""
    half = _kernel_tool_server(task_store=InMemoryTaskStore())
    assert build_capabilities(context(half), modern=True).extensions is None


def test_supplying_an_executor_is_enough_to_get_a_store() -> None:
    """The executor is the switch: a store appears, namespaced like the session store."""
    server = _kernel_tool_server(task_executor=RecordingExecutor())
    assert isinstance(server.task_store, DjangoCacheTaskStore)


def test_passing_task_store_none_is_not_a_request_for_the_default() -> None:
    """Distinguishable from not passed: a way to say that the store is wired later."""
    server = _kernel_tool_server(task_store=None, task_executor=RecordingExecutor())
    assert server.task_store is None
