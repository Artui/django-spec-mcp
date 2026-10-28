"""Creating tasks, moving them between states, and running them.

The three functions that own a task's life: ``create_task`` on the request
path, ``transition_task`` wherever a status changes, and ``run_task`` in the
worker.

Asked here without a spec: the store, the transitions, the worker token, the
store being durable before the hand-off, and ``run_task`` on a task naming no
live tool or on a server with no store, which this module's own ``server``
fixture (a task-running server with no tool) answers. Each question that runs
a registered tool is owed: ``run_task`` completing, a redelivered task, a
protocol error and an unhandled exception failing it, and a worker not charging
the rate limit twice. They register a drf-services tool with a
``task_policy``, which a kernel spec does not take yet (``register_tool``
refuses it with ``ImproperlyConfigured``). The original is kept beside this
file as ``test_task_lifecycle_drfs.py``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.build_worker_token import build_worker_token
from django_spec_mcp.tasks.create_task import create_task
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.types.task_status import TaskStatus
from tests.tasks.conftest import BrokenExecutor, RecordingExecutor


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
    """With no ``tasks/list`` and no session, the id *is* the containment
    boundary — so this is a security property, not a nicety."""
    ids = {_create(store, executor).task_id for _ in range(20)}
    assert len(ids) == 20
    assert all(len(i) > 30 for i in ids)


def test_the_authorization_context_is_stored_not_just_the_principal(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Without the scopes, the worker rebuilds a token that proves nothing and
    every ``ScopeRequired`` binding denies work it had already authorized."""
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
    """By the time ``enqueue`` raises the task exists, so returning an error
    would leave the client with no handle and a record stuck in ``working``."""
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
    """The race this exists for: a cancel landing just after completion.

    Nothing here locks, so the second writer would otherwise win and the client
    would be told its finished work never ran.
    """
    task: Task = _create(store, executor)
    transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={"ok": 1})
    after = transition_task(store, task.task_id, status=TaskStatus.CANCELLED)
    assert after.status is TaskStatus.COMPLETED
    assert after.task.result == {"ok": 1}


def test_a_refused_transition_returns_the_state_that_actually_holds(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Not an error — the caller asked for something already decided, and the
    record it gets back is the answer."""
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


# ----- build_worker_token -----


@pytest.mark.django_db
def test_the_worker_rereads_the_user_rather_than_reconstructing_it(
    django_user_model: Any,
) -> None:
    """A task can sit in a queue long enough for the answers permissions ask to
    change. The point in time that matters is when the work runs."""
    user = django_user_model.objects.create_user(username="u1")
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id=f"user:{user.pk}",
        user_pk=user.pk,
        scopes=("s",),
    )
    token = build_worker_token(record)
    assert token.user.pk == user.pk
    assert token.scopes == ("s",)
    assert token.raw is None


@pytest.mark.django_db
def test_a_deleted_user_degrades_to_anonymous_rather_than_crashing(
    django_user_model: Any,
) -> None:
    """A deleted account should fail the task's permission checks — which is
    what anonymous does — and fail as a denial, not a worker crash."""
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id="user:9999",
        user_pk=9999,
    )
    assert isinstance(build_worker_token(record).user, AnonymousUser)


@pytest.mark.django_db
def test_a_deactivated_user_degrades_to_anonymous_too(django_user_model: Any) -> None:
    """Re-reading the row is only half of honouring a revocation.

    Nothing downstream consults ``is_active`` — ``IsAuthenticated`` is true of
    an inactive user — so a task queued before an account was disabled would
    have run under it, and the re-read would have honoured the deactivation in
    appearance only. This is the check Django's own ``ModelBackend`` makes
    before it will authenticate anyone, applied at the moment the work runs.
    """
    user = django_user_model.objects.create_user(username="gone-away")
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
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
    """Stated because it is the asymmetry, not an oversight to be read past.

    ``raw`` — the backend handle that could be re-validated — is deliberately
    not persisted, so there is nothing here to re-check a token against: a
    grant narrowed or revoked after creation still buys the task the scopes it
    was created with. ``TASK_TTL_MS`` is what bounds that window.
    """
    user = django_user_model.objects.create_user(username="still-here")
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id=f"user:{user.pk}",
        user_pk=user.pk,
        scopes=("invoices:write",),
        audience="https://example.test/mcp",
    )
    token = build_worker_token(record)
    assert token.scopes == ("invoices:write",)
    assert token.audience == "https://example.test/mcp"
    assert token.raw is None


def test_a_task_created_by_an_anonymous_caller_stays_anonymous() -> None:
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id="anonymous",
    )
    assert isinstance(build_worker_token(record).user, AnonymousUser)


def test_with_task_leaves_the_original_untouched() -> None:
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id="anonymous",
    )
    changed = record.with_task(status=TaskStatus.COMPLETED)
    assert record.status is TaskStatus.WORKING
    assert changed.status is TaskStatus.COMPLETED
    assert replace(changed, enqueued=True).to_wire() is changed.task


def test_the_task_is_durable_before_the_executor_is_called(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Normative, and the one ordering that actually matters.

    The spec: a server MUST NOT return a task handle until a ``tasks/get`` for
    that id would resolve. A worker on another machine can pick the task up the
    instant it is queued, so "write after queueing" is a race that hands out an
    id nothing can find.
    """
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
    """Silence here would look like success: the job would finish and the
    client would poll a handle forever."""
    from django.core.exceptions import ImproperlyConfigured

    from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

    bare = MCPServer(name="bare", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="no task store"):
        bare.run_task("anything")
