from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.test import override_settings
from django_service_specs import PermissionCheck, ServiceSpec

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.task_dispatch import maybe_create_task
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.task_status import TaskStatus
from tests.tasks.conftest import RecordingExecutor, context, done, register_kernel_tool


def _task(**kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "task_id": "t",
        "status": TaskStatus.WORKING,
        "created_at": "2026-07-31T10:00:00Z",
        "last_updated_at": "2026-07-31T10:00:00Z",
        "ttl_ms": None,
    }
    defaults.update(kwargs)
    return Task(**defaults)


# ----- Task.to_dict -----


def test_ttl_is_always_present_even_when_null() -> None:
    """``ttlMs: number | null`` in the schema: an omission and a null mean different things."""
    assert _task().to_dict()["ttlMs"] is None


def test_optional_fields_are_omitted_rather_than_nulled() -> None:
    emitted = _task().to_dict()
    assert "pollIntervalMs" not in emitted
    assert "statusMessage" not in emitted


def test_a_status_message_rides_along_when_set() -> None:
    assert _task(status_message="halfway").to_dict()["statusMessage"] == "halfway"


def test_the_status_decides_which_extra_field_appears_not_what_is_stored() -> None:
    """A record holding both a result and an error must not emit a shape the spec lacks."""
    emitted = _task(
        status=TaskStatus.COMPLETED, result={"a": 1}, error={"b": 2}, input_requests={"c": 3}
    ).to_dict()
    assert emitted["result"] == {"a": 1}
    assert "error" not in emitted
    assert "inputRequests" not in emitted


@pytest.mark.parametrize(
    ("status", "key"),
    [
        (TaskStatus.COMPLETED, "result"),
        (TaskStatus.FAILED, "error"),
        (TaskStatus.INPUT_REQUIRED, "inputRequests"),
    ],
)
def test_a_mandatory_field_is_an_empty_object_rather_than_missing(
    status: TaskStatus, key: str
) -> None:
    """The spec makes each mandatory for its status."""
    assert _task(status=status).to_dict()[key] == {}


def test_a_cancelled_task_carries_no_extra_field() -> None:
    emitted = _task(status=TaskStatus.CANCELLED).to_dict()
    assert set(emitted) == {"taskId", "status", "createdAt", "lastUpdatedAt", "ttlMs"}


def test_terminal_statuses_are_exactly_the_three() -> None:
    assert [s for s in TaskStatus if s.is_terminal] == [
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ]


# ----- the policy branches -----


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="policy", auth_backend=AllowAnyBackend(), **kwargs)
    for name, policy in (
        ("p.optional", TaskPolicy.OPTIONAL),
        ("p.required", TaskPolicy.REQUIRED),
    ):
        register_kernel_tool(server, name, done, task_policy=policy)
    return server


def _running(**kwargs: Any) -> MCPServer:
    store = InMemoryTaskStore()
    return _server(task_store=store, task_executor=RecordingExecutor(store), **kwargs)


@pytest.mark.parametrize(
    ("make_server", "declares"),
    [
        pytest.param(_server, True, id="the-server-cannot-run-tasks"),
        pytest.param(_running, False, id="the-client-did-not-declare"),
    ],
)
def test_an_optional_tool_runs_inline_when_a_task_is_not_possible(
    make_server: Callable[[], MCPServer], declares: bool
) -> None:
    """Falling back is the whole point of ``OPTIONAL``: a server without a queue."""
    server = make_server()
    binding = server._tools.get("p.optional")
    assert maybe_create_task(binding, {}, context(server, declares=declares)) is None


def test_an_optional_tool_answers_a_declaring_client_with_a_task() -> None:
    """The control of the two above: the same tool on a server that runs tasks."""
    server = _running()
    result = maybe_create_task(server._tools.get("p.optional"), {}, context(server))
    assert isinstance(result, dict) and result["resultType"] == "task"
    assert server.task_executor.enqueued == [result["taskId"]]


def test_a_required_tool_refuses_a_client_that_did_not_declare() -> None:
    """The case the extension names: unable to service the request without a task handle."""
    server = _running()
    result = maybe_create_task(server._tools.get("p.required"), {}, context(server, declares=False))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY
    assert server.task_executor.enqueued == []


def test_a_required_tool_on_a_server_that_cannot_run_tasks_blames_the_server() -> None:
    """``-32603``, not the capability error."""
    server = _server()
    result = handle_tools_call({"name": "p.required", "arguments": {}}, context(server))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INTERNAL_ERROR
    assert "task_store=" in result.message


def test_a_required_tool_without_a_server_to_run_it_blames_the_server_whatever_was_declared() -> (
    None
):
    """The store is asked of every client: a non-declarer gets -32603, not -32021."""
    server = _server()
    result = handle_tools_call(
        {"name": "p.required", "arguments": {}}, context(server, declares=False)
    )
    assert isinstance(result, JsonRpcError)
    assert result.code != JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY
    assert result.code == JsonRpcErrorCode.INTERNAL_ERROR
    assert result.message == (
        "Tool 'p.required' is registered to run only as a task, but this server has no task "
        "store and executor configured. Pass task_store= and task_executor= to "
        "MCPServer(...), or relax the tool's task_policy."
    )


class _Refuse(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return False


def test_the_capability_is_asked_before_the_scope_gate_and_the_spec_s_checks() -> None:
    """A non-declarer is told -32021 first, so it learns what it can fix before any guard."""
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="gated", auth_backend=AllowAnyBackend(), task_store=store, task_executor=executor
    )
    server.register_tool(
        ServiceSpec(service=done, permissions=[_Refuse()], atomic=False),
        name="p.gated",
        description="x",
        task_policy=TaskPolicy.REQUIRED,
        scopes=("things:write",),
    )
    binding = server._tools.get("p.gated")

    result = maybe_create_task(binding, {}, context(server, declares=False))
    assert isinstance(result, JsonRpcError)
    assert result.code != JsonRpcErrorCode.FORBIDDEN
    assert result.code == JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY
    assert executor.enqueued == []
    declared = maybe_create_task(binding, {}, context(server, scopes=("things:write",)))
    assert isinstance(declared, JsonRpcError)
    assert declared.code == JsonRpcErrorCode.FORBIDDEN


def test_a_scope_denial_stops_the_task_before_it_is_created() -> None:
    """A task is durable, queued work carrying the caller's authorization."""
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="gated", auth_backend=AllowAnyBackend(), task_store=store, task_executor=executor
    )
    register_kernel_tool(
        server, "p.gated", done, task_policy=TaskPolicy.REQUIRED, scopes=("things:write",)
    )
    result = maybe_create_task(server._tools.get("p.gated"), {}, context(server))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.FORBIDDEN
    assert result.data == {"requiredScopes": ["things:write"]}
    assert executor.enqueued == []
    admitted = maybe_create_task(
        server._tools.get("p.gated"), {}, context(server, scopes=("things:write",))
    )
    assert isinstance(admitted, dict) and admitted["resultType"] == "task"


def test_a_binding_predating_tasks_entirely_is_untouched() -> None:
    """getattr default, so a hand-built binding without the field behaves as FORBIDDEN."""

    class _Bare:
        name = "p.bare"

    server = _running()
    assert maybe_create_task(_Bare(), {}, context(server)) is None


# ----- the async path -----


@pytest.mark.django_db(transaction=True)
async def test_the_async_tool_path_hands_back_a_task_too() -> None:
    """The two ``tools/call`` handlers are parallel implementations, so the branch has to exist."""
    server = _running()
    result = await handle_tools_call_async({"name": "p.optional", "arguments": {}}, context(server))
    assert result["resultType"] == "task"
    assert server.task_executor.enqueued == [result["taskId"]]


# ----- config -----


def test_the_task_scalars_come_from_settings_by_default() -> None:
    config = build_mcp_config()
    assert config.task_ttl_ms == 86_400_000
    assert config.task_poll_interval_ms == 5_000


def test_an_explicit_value_wins_over_the_setting() -> None:
    assert build_mcp_config(task_ttl_ms=1_000).task_ttl_ms == 1_000


@override_settings(SPEC_MCP={"TASK_TTL_MS": None, "TASK_POLL_INTERVAL_MS": None})
def test_none_in_settings_means_none_rather_than_unset() -> None:
    """``None`` is a configured answer here (never expire), not an absent one."""
    config = build_mcp_config()
    assert config.task_ttl_ms is None
    assert config.task_poll_interval_ms is None


def test_an_explicit_none_disables_expiry_rather_than_reading_the_setting() -> None:
    """The other half of the same distinction, from the caller's side."""
    assert build_mcp_config(task_ttl_ms=None).task_ttl_ms is None
    assert build_mcp_config(task_poll_interval_ms=None).task_poll_interval_ms is None
    assert build_mcp_config().task_ttl_ms == 86_400_000
    assert build_mcp_config().task_poll_interval_ms == 5_000


# ----- rate limits -----


class _CountingLimiter:
    """Records each charge; refuses once ``deny_after`` charges have landed."""

    def __init__(self, *, deny_after: int | None = None) -> None:
        self.charges: int = 0
        self._deny_after = deny_after

    def consume(self, request: Any, token: Any) -> int | None:
        self.charges += 1
        if self._deny_after is not None and self.charges > self._deny_after:
            return 42
        return None


def _limited_server(limiter: Any, **kwargs: Any) -> MCPServer:
    store = kwargs.pop("store", None) or InMemoryTaskStore()
    server = MCPServer(
        name="limited",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=kwargs.pop("executor", None) or RecordingExecutor(store),
    )
    register_kernel_tool(
        server, "p.limited", done, task_policy=TaskPolicy.OPTIONAL, rate_limits=[limiter]
    )
    return server


def test_a_task_shaped_call_charges_its_rate_limit() -> None:
    """The charge has nowhere else to happen."""
    limiter = _CountingLimiter()
    server = _limited_server(limiter)
    result = maybe_create_task(server._tools.get("p.limited"), {}, context(server))
    assert isinstance(result, dict) and result["resultType"] == "task"
    assert limiter.charges == 1


def test_an_exhausted_quota_refuses_before_a_task_exists() -> None:
    """Refused early: a caller told to retry must leave no queued record behind."""
    limiter = _CountingLimiter(deny_after=0)
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = _limited_server(limiter, store=store, executor=executor)
    result = maybe_create_task(server._tools.get("p.limited"), {}, context(server))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.RATE_LIMITED
    assert result.data == {"retryAfter": 42}
    assert executor.enqueued == []


def test_a_call_that_runs_inline_is_still_charged_exactly_once() -> None:
    """The fallback must not double-charge: an inline run already charges the ordinary way."""
    limiter = _CountingLimiter()
    server = _limited_server(limiter)
    assert (
        maybe_create_task(server._tools.get("p.limited"), {}, context(server, declares=False))
        is None
    )
    assert limiter.charges == 0
    result = handle_tools_call(
        {"name": "p.limited", "arguments": {}}, context(server, declares=False)
    )
    assert result["structuredContent"] == {"done": "yes"}
    assert limiter.charges == 1
