from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.build_worker_token import build_worker_token
from django_spec_mcp.tasks.create_task import create_task
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.types.task_status import TaskStatus
from tests.tasks.conftest import (
    BrokenExecutor,
    RecordingExecutor,
    context,
    done,
    register_kernel_tool,
)


def _create(store: Any, executor: Any, **kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "tool_name": "tasks.optional",
        "arguments": {},
        "token": TokenInfo(user=None),
        "ttl_ms": 60_000,
        "poll_interval_ms": 500,
    }
    defaults.update(kwargs)
    return create_task(store=store, executor=executor, **defaults)


# ----- create_task -----


def test_the_handle_carries_what_the_client_needs_to_poll(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    assert task.status is TaskStatus.WORKING
    assert task.ttl_ms == 60_000
    assert task.poll_interval_ms == 500
    assert task.created_at == task.last_updated_at


def test_ids_are_unguessable(store: InMemoryTaskStore, executor: RecordingExecutor) -> None:
    """With no ``tasks/list`` and no session, the id *is* the containment boundary."""
    ids = {_create(store, executor).task_id for _ in range(20)}
    assert len(ids) == 20
    assert all(len(i) > 30 for i in ids)


def test_the_authorization_context_is_stored_not_just_the_principal(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Without the scopes the worker rebuilds a token that proves nothing, and work is denied."""
    task: Task = _create(
        store, executor, token=TokenInfo(user=None, scopes=("a", "b"), audience="aud")
    )
    record: TaskRecord = store.get(task.task_id)
    assert record.scopes == ("a", "b")
    assert record.audience == "aud"
    assert record.principal_id == "anonymous"


def test_a_broker_that_is_down_produces_a_failed_task_not_a_failed_request(
    store: InMemoryTaskStore,
) -> None:
    """By the time ``enqueue`` raises the task exists."""
    task: Task = _create(store, BrokenExecutor())
    assert task.status is TaskStatus.FAILED
    assert "broker unreachable" in (task.status_message or "")
    assert store.get(task.task_id).status is TaskStatus.FAILED


def test_a_successful_hand_off_marks_the_task_claimable(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    assert store.get(task.task_id).enqueued is True


def test_a_failed_hand_off_leaves_the_task_unclaimable(store: InMemoryTaskStore) -> None:
    task: Task = _create(store, BrokenExecutor())
    assert store.get(task.task_id).enqueued is False


# ----- transition_task -----


def test_a_finished_task_cannot_be_reopened(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The race this exists for: a cancel landing just after completion."""
    task: Task = _create(store, executor)
    transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={"ok": 1})
    after = transition_task(store, task.task_id, status=TaskStatus.CANCELLED)
    assert after.status is TaskStatus.COMPLETED
    assert after.task.result == {"ok": 1}


def test_a_refused_transition_returns_the_state_that_actually_holds(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Not an error — the caller asked for something already decided."""
    task: Task = _create(store, executor)
    transition_task(store, task.task_id, status=TaskStatus.CANCELLED)
    assert transition_task(store, task.task_id, status=TaskStatus.COMPLETED).status is (
        TaskStatus.CANCELLED
    )


def test_transitioning_an_unknown_task_is_none(store: InMemoryTaskStore) -> None:
    assert transition_task(store, "nope", status=TaskStatus.COMPLETED) is None


def test_a_real_transition_moves_the_timestamp(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    moved = transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={})
    assert moved.task.last_updated_at >= task.last_updated_at


# ----- run_task -----


def _ask(server: MCPServer, name: str) -> str:
    """Call ``name`` as a client declaring the extension, and return the task id."""
    handle: Any = handle_tools_call({"name": name, "arguments": {}}, context(server))
    assert handle["resultType"] == "task", handle
    return handle["taskId"]


@pytest.mark.django_db
def test_running_a_task_completes_it_with_the_tool_result(
    policy_server: MCPServer, store: InMemoryTaskStore
) -> None:
    task_id = _ask(policy_server, "tasks.optional")
    assert store.get(task_id).status is TaskStatus.WORKING
    policy_server.run_task(task_id)
    record: TaskRecord = store.get(task_id)
    assert record.status is TaskStatus.COMPLETED
    assert record.task.result["structuredContent"] == {"done": "yes"}


@pytest.mark.django_db
def test_a_required_tool_runs_as_a_task_too(
    policy_server: MCPServer, store: InMemoryTaskStore
) -> None:
    """The worker's run is the only place a ``REQUIRED`` tool's work can happen."""
    task_id = _ask(policy_server, "tasks.required")
    policy_server.run_task(task_id)
    record: TaskRecord = store.get(task_id)
    assert record.status is TaskStatus.COMPLETED, record.task.error
    assert record.task.result["structuredContent"] == {"done": "yes"}


@pytest.mark.django_db
def test_a_selector_tool_runs_as_a_task(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """A selector takes a ``task_policy`` as a service does."""
    server = MCPServer(
        name="tasks-selector",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: {"row": "one"},
            permissions=[Unrestricted()],
        ),
        name="tasks.row",
        description="x",
        task_policy=TaskPolicy.OPTIONAL,
    )
    task_id = _ask(server, "tasks.row")
    server.run_task(task_id)
    record: TaskRecord = store.get(task_id)
    assert record.status is TaskStatus.COMPLETED
    assert record.task.result["structuredContent"] == {"row": "one"}


class _Switch(PermissionCheck):
    """Admits while ``admits`` holds, counting how often it was asked."""

    def __init__(self) -> None:
        self.admits = True
        self.asked = 0

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.asked += 1
        return self.admits


def _gated(store: InMemoryTaskStore, executor: RecordingExecutor) -> tuple[MCPServer, _Switch]:
    check = _Switch()
    server = MCPServer(
        name="tasks-spec-gated",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
    )
    server.register_tool(
        ServiceSpec(service=done, permissions=[check]),
        name="tasks.gated",
        description="x",
        task_policy=TaskPolicy.OPTIONAL,
    )
    return server, check


@pytest.mark.django_db
def test_a_caller_the_spec_refuses_is_given_no_task(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The spec's own ``permissions`` are asked before anything durable exists."""
    server, check = _gated(store, executor)
    check.admits = False
    result = handle_tools_call({"name": "tasks.gated", "arguments": {}}, context(server))
    assert isinstance(result, JsonRpcError) and result.code == -32006
    assert executor.enqueued == []


@pytest.mark.django_db
def test_the_worker_asks_the_spec_again(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Admitted when the task was created and refused by the time it runs."""
    server, check = _gated(store, executor)
    task_id = _ask(server, "tasks.gated")
    asked_at_creation = check.asked
    check.admits = False
    server.run_task(task_id)
    record: TaskRecord = store.get(task_id)
    assert check.asked > asked_at_creation
    assert record.status is TaskStatus.FAILED
    assert record.task.error["code"] == -32006


@pytest.mark.django_db
def test_a_redelivered_task_runs_once(server: MCPServer, store: InMemoryTaskStore) -> None:
    """Queues deliver at least once."""
    calls: list[int] = []

    def counting() -> dict[str, str]:
        calls.append(1)
        return {"n": str(len(calls))}

    register_kernel_tool(server, "tasks.counting", counting, task_policy=TaskPolicy.OPTIONAL)
    task_id = _ask(server, "tasks.counting")
    server.run_task(task_id)
    server.run_task(task_id)
    assert calls == [1]


@pytest.mark.django_db
def test_a_protocol_error_becomes_a_failed_task(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """A JSON-RPC error has no response left to ride in — the client is long gone."""
    task: Task = _create(store, executor, tool_name="does.not.exist")
    server.run_task(task.task_id)
    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.FAILED
    assert record.task.error["code"] == -32602


@pytest.mark.django_db
def test_a_refusal_the_worker_meets_keeps_its_data_on_the_record(
    server: MCPServer, store: InMemoryTaskStore
) -> None:
    """A JSON-RPC error carrying data keeps it, so the client sees the scopes it needed."""
    register_kernel_tool(
        server, "tasks.gated", done, task_policy=TaskPolicy.OPTIONAL, scopes=("things:write",)
    )
    handle: Any = handle_tools_call(
        {"name": "tasks.gated", "arguments": {}}, context(server, scopes=("things:write",))
    )
    record: TaskRecord = store.get(handle["taskId"])
    store.save(replace(record, scopes=()))
    server.run_task(handle["taskId"])
    failed: TaskRecord = store.get(handle["taskId"])
    assert failed.status is TaskStatus.FAILED
    assert failed.task.error["code"] == -32006
    assert failed.task.error["data"] == {"requiredScopes": ["things:write"]}


@pytest.mark.django_db
def test_an_unhandled_exception_fails_the_task_and_still_propagates(
    server: MCPServer, store: InMemoryTaskStore
) -> None:
    """Failing the task stops the client polling forever."""

    def explode() -> Any:
        raise RuntimeError("boom")

    register_kernel_tool(server, "tasks.explode", explode, task_policy=TaskPolicy.OPTIONAL)
    task_id = _ask(server, "tasks.explode")
    with pytest.raises(RuntimeError, match="boom"):
        server.run_task(task_id)
    record: TaskRecord = store.get(task_id)
    assert record.status is TaskStatus.FAILED
    assert record.task.status_message == "The task raised an unhandled exception: boom"


@pytest.mark.django_db
def test_a_worker_does_not_charge_the_rate_limit_a_second_time(
    server: MCPServer, store: InMemoryTaskStore
) -> None:
    """The limit was consumed when the client asked."""
    consumed: list[int] = []

    class _Limiter:
        def consume(self, request: Any, token: Any) -> int | None:
            consumed.append(1)
            return None

    register_kernel_tool(
        server, "tasks.limited", done, task_policy=TaskPolicy.OPTIONAL, rate_limits=[_Limiter()]
    )
    task_id = _ask(server, "tasks.limited")
    assert consumed == [1]
    server.run_task(task_id)
    assert consumed == [1]
    assert store.get(task_id).status is TaskStatus.COMPLETED


@pytest.mark.django_db
def test_a_task_worker_hands_the_seeds_to_the_tool_it_runs(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The worker's context carries the server's ``pool_seeds``."""

    def whoami(*, tenant: str) -> dict[str, str]:
        return {"tenant": tenant}

    server = MCPServer(
        name="tasks-seeds",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )
    register_kernel_tool(server, "tenant.whoami", whoami, task_policy=TaskPolicy.OPTIONAL)
    task_id = _ask(server, "tenant.whoami")
    server.run_task(task_id)
    assert store.get(task_id).task.result["structuredContent"] == {"tenant": "acme"}


# ----- build_worker_token -----


def _worker_record(**fields: Any) -> TaskRecord:
    """A record as the worker reads it; only the authorization fields vary."""
    task = Task(
        task_id="t", status=TaskStatus.WORKING, created_at="x", last_updated_at="x", ttl_ms=None
    )
    return TaskRecord(task=task, tool_name="t", arguments={}, **fields)


@pytest.mark.django_db
def test_the_worker_rereads_the_user_rather_than_reconstructing_it(
    django_user_model: Any,
) -> None:
    """A task can sit in a queue long enough for the answers permissions ask to change."""
    user = django_user_model.objects.create_user(username="u1")
    record = _worker_record(
        principal_id=f"user:{user.pk}",
        user_pk=user.pk,
        scopes=("s",),
    )
    token = build_worker_token(record)
    assert token.user.pk == user.pk
    assert token.scopes == ("s",)
    assert token.raw is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("principal_id", "user_pk"),
    [
        # A deleted account should fail the task's permission checks, which is
        pytest.param("user:9999", 9999, id="a-deleted-user"),
        pytest.param("anonymous", None, id="an-anonymous-caller"),
    ],
)
def test_a_user_the_worker_cannot_find_runs_as_anonymous(
    principal_id: str, user_pk: int | None
) -> None:
    record = _worker_record(principal_id=principal_id, user_pk=user_pk)
    assert isinstance(build_worker_token(record).user, AnonymousUser)


@pytest.mark.django_db
def test_a_deactivated_user_degrades_to_anonymous_too(django_user_model: Any) -> None:
    """Re-reading the row is only half of honouring a revocation."""
    user = django_user_model.objects.create_user(username="gone-away")
    record = _worker_record(
        principal_id=f"user:{user.pk}",
        user_pk=user.pk,
        scopes=("s",),
    )
    assert build_worker_token(record).user.pk == user.pk

    user.is_active = False
    user.save(update_fields=["is_active"])
    assert isinstance(build_worker_token(record).user, AnonymousUser)


@pytest.mark.django_db
def test_the_scopes_a_worker_replays_are_the_ones_frozen_at_creation(
    django_user_model: Any,
) -> None:
    """Stated because it is the asymmetry, not an oversight to be read past."""
    user = django_user_model.objects.create_user(username="still-here")
    record = _worker_record(
        principal_id=f"user:{user.pk}",
        user_pk=user.pk,
        scopes=("invoices:write",),
        audience="https://example.test/mcp",
    )
    token = build_worker_token(record)
    assert token.scopes == ("invoices:write",)
    assert token.audience == "https://example.test/mcp"
    assert token.raw is None


def test_with_task_leaves_the_original_untouched() -> None:
    record = _worker_record(
        principal_id="anonymous",
    )
    changed = record.with_task(status=TaskStatus.COMPLETED)
    assert record.status is TaskStatus.WORKING
    assert changed.status is TaskStatus.COMPLETED
    assert replace(changed, enqueued=True).to_wire() is changed.task


def test_the_task_is_durable_before_the_executor_is_called(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Normative, and the one ordering that actually matters."""
    _create(store, executor)
    assert executor.seen_in_store == [True]


@pytest.mark.django_db
def test_running_an_unknown_task_is_a_no_op(server: MCPServer) -> None:
    server.run_task("never-existed")


@pytest.mark.django_db
def test_running_a_task_that_never_reached_the_queue_is_a_no_op(
    server: MCPServer, store: InMemoryTaskStore
) -> None:
    task: Task = _create(store, BrokenExecutor())
    server.run_task(task.task_id)
    assert store.get(task.task_id).status is TaskStatus.FAILED


def test_run_task_on_a_server_with_no_store_refuses_loudly() -> None:
    """Silence here would look like success: the client would poll a handle forever."""
    from django.core.exceptions import ImproperlyConfigured

    from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

    bare = MCPServer(name="bare", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="no task store"):
        bare.run_task("anything")
