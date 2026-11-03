"""A task whose service asks for input parks in ``input_required`` and resumes.

The worker runs the service; ``AdditionalInputRequired`` there, for a client
whose creating request declared form elicitation, moves the task to
``input_required`` with its ``inputRequests`` instead of completing it with an
``isError`` result. ``tasks/update`` answers it: the answer merges into the
recorded arguments as a retry's answers merge at task creation, the spec's
checks are asked on the merged arguments, the round is charged, and the task
is queued again. The worker re-runs with the merged arguments, under the round
budget the inline call has.

Every assertion reads the wire (``tasks/get``) or what the executor and the
limiter saw, so the file asks the same questions of a release without the
parking.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest
from django.core.cache import cache
from django_service_specs import (
    AdditionalInputRequired,
    DataclassValidator,
    PermissionCheck,
    ServerArguments,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tasks_cancel import handle_tasks_cancel
from django_spec_mcp.handlers.handle_tasks_get import handle_tasks_get
from django_spec_mcp.handlers.handle_tasks_update import handle_tasks_update
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.tasks.create_task import create_task
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.utils import input_request_key
from django_spec_mcp.types.task_status import TaskStatus
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.elicitation.conftest import TOOL, DeleteRows, delete_rows_kernel
from tests.tasks.conftest import BrokenExecutor, RecordingExecutor, context
from tests.utils import tool_error


def nag_kernel(*, count: int, confirmed: bool = False, reason: str = "") -> dict[str, Any]:  # noqa: ARG001
    """A service no answer satisfies: it asks again every time."""
    raise AdditionalInputRequired("Are you sure?", schema={"confirmed": {"type": "boolean"}})


class _Limiter:
    """Records each charge; refuses every one once ``refuse`` is set."""

    def __init__(self) -> None:
        self.charges: int = 0
        self.refuse: bool = False

    def consume(self, request: Any, token: Any) -> int | None:
        self.charges += 1
        return 30 if self.refuse else None


class _RefusesAReason(PermissionCheck):
    """Refuses a call carrying a ``reason``, which only the second answer supplies."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return not arguments.get("reason")


class _World:
    """A task-running server holding ``rows.delete`` (or ``service``) as a task."""

    def __init__(
        self,
        *,
        service: Any = delete_rows_kernel,
        check: PermissionCheck | None = None,
        store: Any = None,
        server_arguments: ServerArguments | None = None,
        **config: Any,
    ) -> None:
        self.store = store if store is not None else InMemoryTaskStore()
        self.executor = RecordingExecutor(self.store)
        self.limiter = _Limiter()
        self.server = MCPServer(
            name="asks",
            auth_backend=AllowAnyBackend(),
            task_store=self.store,
            task_executor=self.executor,
            config=build_mcp_config(**config),
        )
        self.server.register_tool(
            ServiceSpec(
                service=service,
                validator=DataclassValidator(DeleteRows),
                permissions=[check or Unrestricted()],
                atomic=False,
            ),
            name=TOOL,
            description="Delete rows.",
            task_policy=TaskPolicy.OPTIONAL,
            rate_limits=[self.limiter],
            **({"server_arguments": server_arguments} if server_arguments else {}),
        )

    def ctx(self, *, elicitation: Any = None) -> MCPCallContext:
        """A request declaring the tasks extension and ``elicitation``: forms when
        ``None``, nothing when ``False``."""
        capabilities: dict[str, Any] = {"extensions": {TASKS_EXTENSION_ID: {}}}
        if elicitation is not False:
            capabilities["elicitation"] = {} if elicitation is None else elicitation
        return replace(context(self.server), client_capabilities=capabilities)

    def create(self, count: int, **ctx: Any) -> str:
        """Call the tool as a task; return the task id."""
        handle: Any = handle_tools_call(
            {"name": TOOL, "arguments": {"count": count}}, self.ctx(**ctx)
        )
        assert handle["resultType"] == "task", handle
        return str(handle["taskId"])

    def start(self, count: int, **ctx: Any) -> str:
        """Call the tool as a task and run its worker once; return the task id."""
        task_id = self.create(count, **ctx)
        self.server.run_task(task_id)
        return task_id

    def get(self, task_id: str) -> dict[str, Any]:
        out: Any = handle_tasks_get({"taskId": task_id}, self.ctx())
        assert isinstance(out, dict), out
        return out

    def answer(self, task_id: str, answer: dict[str, Any], key: str | None = None) -> Any:
        """Answer the task's one outstanding request, under the key it was asked under."""
        if key is None:
            (key,) = self.get(task_id)["inputRequests"]
        return handle_tasks_update({"taskId": task_id, "inputResponses": {key: answer}}, self.ctx())


def _accept(**content: Any) -> dict[str, Any]:
    return {"action": "accept", "content": content}


def test_a_task_asks_waits_and_completes_with_the_answer() -> None:
    world = _World()
    task_id = world.start(500)

    parked = world.get(task_id)
    assert parked["status"] == "input_required", parked
    ((key, request),) = parked["inputRequests"].items()
    assert request["method"] == "elicitation/create"
    assert request["params"]["message"] == "500 rows match. Confirm to proceed."
    assert request["params"]["requestedSchema"]["properties"] == {"confirmed": {"type": "boolean"}}

    assert world.answer(task_id, _accept(confirmed=True), key) == {}
    assert world.get(task_id)["status"] == "working"
    assert world.executor.enqueued == [task_id, task_id]
    assert world.store.get(task_id).input_responses == {key: _accept(confirmed=True)}

    world.server.run_task(task_id)
    done = world.get(task_id)
    assert done["status"] == "completed", done
    assert done["result"]["structuredContent"] == {
        "deleted": 500,
        "confirmed": True,
        "reason": "",
    }


def test_a_second_question_is_asked_under_a_new_key_and_keeps_the_first_answer() -> None:
    """The spec forbids reusing a key over a task's lifetime, and the first
    answer must survive the second question, as it does inline."""
    world = _World()
    task_id = world.start(1500)
    (first,) = world.get(task_id)["inputRequests"]
    world.answer(task_id, _accept(confirmed=True), first)
    world.server.run_task(task_id)

    parked = world.get(task_id)
    assert parked["status"] == "input_required", parked
    (second,) = parked["inputRequests"]
    assert second != first
    assert parked["inputRequests"][second]["params"]["message"] == (
        "Deleting more than 1000 rows needs a reason."
    )
    world.answer(task_id, _accept(reason="cleanup"), second)
    world.server.run_task(task_id)

    assert world.get(task_id)["result"]["structuredContent"] == {
        "deleted": 1500,
        "confirmed": True,
        "reason": "cleanup",
    }
    assert set(world.store.get(task_id).input_responses) == {first, second}


@pytest.mark.parametrize("elicitation", [False, {"url": {}}])
def test_a_client_that_cannot_be_asked_gets_the_result_it_gets_today(elicitation: Any) -> None:
    """No form elicitation declared on the creating request: the task is never
    parked, and completes with the ``isError`` result naming what is missing."""
    world = _World()
    task_id = world.start(500, elicitation=elicitation)

    done = world.get(task_id)
    assert done["status"] == "completed", done
    error = tool_error(done["result"])
    assert error["type"] == "input_required"
    assert error["requestedInput"] == {"confirmed": {"type": "boolean"}}


@pytest.mark.parametrize("bound", [None, ServerArguments(values={"reason": "audit"})])
def test_the_round_budget_ends_the_exchange_as_inline(bound: ServerArguments | None) -> None:
    """Two rounds allowed: the service is asked twice and the third question
    becomes the ``isError`` result, never a third park. With a bound value too,
    since the replay's round binds the arguments as recorded, which hold it."""
    world = _World(service=nag_kernel, max_input_rounds=2, server_arguments=bound)
    task_id = world.start(500)
    for _ in range(2):
        assert world.get(task_id)["status"] == "input_required"
        world.answer(task_id, _accept(confirmed=True))
        world.server.run_task(task_id)

    done = world.get(task_id)
    assert done["status"] == "completed", done
    assert tool_error(done["result"])["type"] == "input_required"


def test_an_answer_long_after_the_question_still_completes_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task waits as long as its TTL, so the answer is not held to the
    inline ``requestState`` lifetime: past it, a stored signature would no longer
    verify and the earlier answer and the round would silently drop."""
    world = _World(input_request_ttl_seconds=5)
    task_id = world.start(1500)
    world.answer(task_id, _accept(confirmed=True))
    world.server.run_task(task_id)

    later: float = time.time() + 3600
    monkeypatch.setattr(time, "time", lambda: later)
    world.answer(task_id, _accept(reason="cleanup"))
    world.server.run_task(task_id)

    assert world.get(task_id)["result"]["structuredContent"]["confirmed"] is True


@pytest.mark.parametrize(
    ("action", "error_type"), [("decline", "input_declined"), ("cancel", "input_cancelled")]
)
def test_a_declined_answer_ends_the_task_as_the_inline_refusal_and_is_charged(
    action: str, error_type: str
) -> None:
    world = _World()
    task_id = world.start(500)

    assert world.answer(task_id, {"action": action}) == {}

    done = world.get(task_id)
    assert done["status"] == "completed", done
    assert tool_error(done["result"])["type"] == error_type
    assert world.executor.enqueued == [task_id]
    assert world.limiter.charges == 2


def test_each_answering_round_is_charged_once() -> None:
    """The worker charges nothing, so the round the client answers pays, as an
    inline retry does: without it a parked task re-ran its service for free."""
    world = _World()
    task_id = world.start(500)
    assert world.limiter.charges == 1
    world.answer(task_id, _accept(confirmed=True))
    world.server.run_task(task_id)
    assert world.limiter.charges == 2


def test_a_refused_charge_leaves_the_task_waiting() -> None:
    world = _World()
    task_id = world.start(500)
    world.limiter.refuse = True

    refused = world.answer(task_id, _accept(confirmed=True))

    assert isinstance(refused, JsonRpcError), refused
    assert world.get(task_id)["status"] == "input_required"
    assert world.executor.enqueued == [task_id]


def test_the_checks_are_asked_on_the_merged_arguments_before_the_charge() -> None:
    """The check refuses ``reason``, which only the second answer supplies:
    the answer is refused, uncharged, and the task keeps waiting for one it admits."""
    world = _World(check=_RefusesAReason())
    task_id = world.start(1500)
    world.answer(task_id, _accept(confirmed=True))
    world.server.run_task(task_id)
    charged: int = world.limiter.charges

    refused = world.answer(task_id, _accept(reason="cleanup"))

    assert isinstance(refused, JsonRpcError), refused
    assert world.limiter.charges == charged
    assert world.get(task_id)["status"] == "input_required"


def test_a_stale_delivery_does_not_run_a_waiting_task() -> None:
    """The queue may deliver the first run again: a waiting task is not claimable."""
    world = _World()
    task_id = world.start(500)

    world.server.run_task(task_id)

    assert world.get(task_id)["status"] == "input_required"


def test_a_task_that_cannot_be_queued_again_fails() -> None:
    """The hand-off after an answer fails as task creation's does: visibly."""
    world = _World()
    task_id = world.start(500)
    world.server._task_executor = BrokenExecutor()

    assert world.answer(task_id, _accept(confirmed=True)) == {}

    failed = world.get(task_id)
    assert failed["status"] == "failed", failed
    assert "broker unreachable" in failed["statusMessage"]


def test_a_server_with_no_executor_left_fails_the_answered_task() -> None:
    world = _World()
    task_id = world.start(500)
    world.server._task_executor = None

    assert world.answer(task_id, _accept(confirmed=True)) == {}

    failed = world.get(task_id)
    assert failed["status"] == "failed", failed
    assert "no task executor" in failed["statusMessage"]


@pytest.mark.parametrize("meanwhile", ["cancelled", "expired"])
def test_a_task_gone_or_cancelled_while_its_service_asked_is_not_parked(meanwhile: str) -> None:
    """A cancel, or the record's expiry, landing while the service ran wins: the
    question it then asks parks nothing."""
    task: dict[str, str] = {}

    def asks_after_the_task_ends(*, count: int, confirmed: bool = False, reason: str = "") -> Any:  # noqa: ARG001
        if meanwhile == "cancelled":
            handle_tasks_cancel({"taskId": task["id"]}, world.ctx())
        else:
            world.store.delete(task["id"])
        raise AdditionalInputRequired("Sure?", schema={"confirmed": {"type": "boolean"}})

    world = _World(service=asks_after_the_task_ends)
    task["id"] = world.create(500)

    world.server.run_task(task["id"])

    record = world.store.get(task["id"])
    if meanwhile == "cancelled":
        assert record.status is TaskStatus.CANCELLED
        assert record.input_round == 0
    else:
        assert record is None


@pytest.mark.django_db
def test_a_task_queued_by_0_6_0_runs_as_it_did() -> None:
    """A record the cache kept across the upgrade carries no capability, so its
    service's question is the ``isError`` result 0.6.0 gave, not a park."""
    store = DjangoCacheTaskStore(namespace="asks-upgrade")
    world = _World(store=store)
    task_id = world.create(500)
    raw: dict[str, Any] = cache.get(store._key(task_id))
    for key in ("canElicit", "inputRound"):
        del raw[key]
    cache.set(store._key(task_id), raw)

    world.server.run_task(task_id)

    done = world.get(task_id)
    assert done["status"] == "completed", done
    assert tool_error(done["result"])["type"] == "input_required"


class _ChangesMeanwhile(PermissionCheck):
    """Runs ``act`` once, as the check of the answered round: a request landing
    while ``tasks/update`` admits the answer, after it read the task."""

    def __init__(self, act: Any) -> None:
        self.act = act
        self.fired: bool = False

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:  # noqa: ARG002
        if arguments.get("confirmed") and not self.fired:
            self.fired = True
            self.act()
        return True


def _counting(runs: list[int]) -> Any:
    """``delete_rows_kernel`` noting each run that carries the answer."""

    def counted(*, count: int, confirmed: bool = False, reason: str = "") -> dict[str, Any]:
        if confirmed:
            runs.append(count)
        return delete_rows_kernel(count=count, confirmed=confirmed, reason=reason)

    return counted


@pytest.mark.parametrize("meanwhile", ["cancelled", "expired"])
def test_a_task_ended_while_its_answer_is_admitted_stays_ended(meanwhile: str) -> None:
    """``tasks/update`` reads the task, then admits the answer (the checks and the
    charge), then saves ``working``: the save is built from the record as it stands
    then, so a cancel, or the record's expiry, landing in between is not undone and
    the cancelled task's service does not run. Fails without the re-read in
    ``resume_task``."""
    runs: list[int] = []
    task: dict[str, str] = {}

    def act() -> None:
        if meanwhile == "cancelled":
            assert handle_tasks_cancel({"taskId": task["id"]}, world.ctx()) == {}
        else:
            world.store.delete(task["id"])

    world = _World(service=_counting(runs), check=_ChangesMeanwhile(act))
    task["id"] = world.start(500)
    enqueued = list(world.executor.enqueued)

    assert world.answer(task["id"], _accept(confirmed=True)) == {}
    world.server.run_task(task["id"])

    record = world.store.get(task["id"])
    if meanwhile == "cancelled":
        assert record.status is TaskStatus.CANCELLED
    else:
        assert record is None
    assert runs == []
    assert world.executor.enqueued == enqueued


@pytest.mark.parametrize("worker_ran", [True, False])
def test_a_duplicate_answer_runs_the_service_once(worker_ran: bool) -> None:
    """A client retrying ``tasks/update`` can land the second while the first
    admits. The second resumes the task (and, here, its worker runs); the first
    then finds the task no longer waiting and queues nothing, rather than saving
    its record over a task that moved on and running the service again. Fails
    without the guard in ``resume_task``."""
    runs: list[int] = []
    task: dict[str, str] = {}

    def duplicate() -> None:
        assert world.answer(task["id"], _accept(confirmed=True)) == {}
        if worker_ran:
            world.server.run_task(task["id"])

    world = _World(service=_counting(runs), check=_ChangesMeanwhile(duplicate))
    task["id"] = world.start(500)

    assert world.answer(task["id"], _accept(confirmed=True)) == {}
    world.server.run_task(task["id"])

    assert runs == [500]
    assert world.executor.enqueued == [task["id"]] * 2
    assert world.store.get(task["id"]).status is TaskStatus.COMPLETED


def test_a_late_answer_leaves_the_next_rounds_question_in_place() -> None:
    """A duplicate ``tasks/update`` lands while the first admits: it resumes the
    task, its worker runs, and the service asks round 2, parking the task again.
    The first then finds the task waiting on a question it did not answer, and
    queues nothing, rather than resuming it, wiping round 2's question and running
    the service once more to ask it again. Fails without the round and request
    guard in ``resume_task``."""
    runs: list[int] = []
    task: dict[str, str] = {}

    def duplicate_runs_and_parks_again() -> None:
        assert world.answer(task["id"], _accept(confirmed=True), input_request_key(1)) == {}
        world.server.run_task(task["id"])

    world = _World(service=_counting(runs), check=_ChangesMeanwhile(duplicate_runs_and_parks_again))
    task["id"] = world.start(1500)

    assert world.answer(task["id"], _accept(confirmed=True), input_request_key(1)) == {}

    parked = world.get(task["id"])
    assert parked["status"] == "input_required", parked
    assert list(parked["inputRequests"]) == [input_request_key(2)]
    assert parked["inputRequests"][input_request_key(2)]["params"]["message"] == (
        "Deleting more than 1000 rows needs a reason."
    )
    assert world.store.get(task["id"]).input_round == 2
    assert world.executor.enqueued == [task["id"]] * 2
    assert runs == [1500]


@pytest.mark.parametrize("moved", ["round", "requests", "status"])
def test_an_answer_resumes_only_the_question_it_read(moved: str) -> None:
    """Each conjunct of the guard in ``resume_task`` alone. While the answer is
    admitted, the task's round moves with its requests as they were, a further
    request joins the one answered, or its status leaves ``input_required`` with
    the rest as it was: the answer resumes none of them, saves nothing and queues
    nothing."""
    task: dict[str, str] = {}

    def act() -> None:
        record = world.store.get(task["id"])
        if moved == "round":
            world.store.save(replace(record, input_round=record.input_round + 1))
        elif moved == "requests":
            world.store.save(
                record.with_task(input_requests={**record.task.input_requests, "k2": {}})
            )
        else:
            world.store.save(record.with_task(status=TaskStatus.WORKING))

    world = _World(check=_ChangesMeanwhile(act))
    task["id"] = world.start(500)

    assert world.answer(task["id"], _accept(confirmed=True), input_request_key(1)) == {}

    record = world.store.get(task["id"])
    assert record.input_responses == {}
    assert not record.enqueued
    assert world.executor.enqueued == [task["id"]]


class _CountingStore(InMemoryTaskStore):
    """Notes each read and save once ``calls`` is armed."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] | None = None

    def get(self, task_id: str) -> Any:
        if self.calls is not None:
            self.calls.append("get")
        return super().get(task_id)

    def save(self, record: Any) -> None:
        if self.calls is not None:
            self.calls.append("save")
        super().save(record)


def test_the_park_reads_the_task_once_and_saves_it_once() -> None:
    """From the service's question on, the park is one read and one save: the
    round, the status and the question land in one record, built from the task as
    the run left it, so the progress the service reported is kept."""
    store = _CountingStore()

    def asks(*, count: int, progress: Any, confirmed: bool = False, reason: str = "") -> Any:  # noqa: ARG001
        progress(1, total=2, message="Counting")
        store.calls = []
        raise AdditionalInputRequired("Sure?", schema={"confirmed": {"type": "boolean"}})

    world = _World(service=asks, store=store)
    task_id = world.create(500)

    world.server.run_task(task_id)

    assert store.calls == ["get", "save"]
    store.calls = None
    record = store.get(task_id)
    assert record.status is TaskStatus.INPUT_REQUIRED
    assert record.input_round == 1
    assert set(record.task.input_requests) == {input_request_key(1)}
    assert (record.progress, record.total) == (1, 2)


def test_a_transition_sets_the_round_only_when_asked_and_only_when_it_happens() -> None:
    """``input_round`` lands with the transition it is given to; left out, the round
    stays as it was; and a transition the terminal guard refuses sets none."""
    world = _World()
    task_id = world.create(5)

    transition_task(world.store, task_id, status=TaskStatus.INPUT_REQUIRED, input_round=3)
    assert world.store.get(task_id).input_round == 3
    transition_task(world.store, task_id, status=TaskStatus.WORKING)
    assert world.store.get(task_id).input_round == 3
    transition_task(world.store, task_id, status=TaskStatus.CANCELLED)
    transition_task(world.store, task_id, status=TaskStatus.INPUT_REQUIRED, input_round=4)

    record = world.store.get(task_id)
    assert (record.status, record.input_round) == (TaskStatus.CANCELLED, 3)


def test_the_answer_is_saved_before_the_task_is_queued() -> None:
    """An executor may run the worker before ``enqueue`` returns: the task must
    already read ``working`` and queued then, or the worker finds it waiting and
    leaves it so for good."""
    world = _World()
    task_id = world.start(500)

    class _Inline:
        def enqueue(self, queued: str) -> None:
            world.server.run_task(queued)

    world.server._task_executor = _Inline()

    assert world.answer(task_id, _accept(confirmed=True)) == {}

    assert world.get(task_id)["status"] == "completed"


def _hand_parked(keys: dict[str, Any], *, count: int = 5) -> tuple[_World, str]:
    """A registered tool's task parked as 0.6.0's public API did it: through
    ``transition_task``, under the caller's own keys.

    ``count`` of 5 is a call the service never asks about, so what happens next is
    the package's doing and not the service's."""
    world = _World()
    task_id = world.create(count)
    record = world.store.get(task_id)
    world.store.save(replace(record, enqueued=False))
    transition_task(world.store, task_id, status=TaskStatus.INPUT_REQUIRED, input_requests=keys)
    return world, task_id


def _seen(world: _World, task_id: str) -> tuple[Any, ...]:
    """What an answer could have caused: queueings, charges and the recorded call."""
    record = world.store.get(task_id)
    return (len(world.executor.enqueued), world.limiter.charges, record.arguments)


@pytest.mark.parametrize(
    "answers",
    [
        [{"k1": {"name": "Ada"}, "k2": 2}],
        [{"k1": {"name": "Ada"}}, {"k2": 2}],
    ],
    ids=["in-one-update", "in-two-updates"],
)
def test_a_task_parked_by_hand_is_answered_as_in_0_6_0(answers: list[dict[str, Any]]) -> None:
    """Answering every key sets it ``working`` and nothing else: no queueing, no
    charge and no merge into the arguments, as when only ``transition_task`` parked
    tasks. The tool is registered, which is what makes the package's own resuming
    reachable."""
    world, task_id = _hand_parked({"k1": {}, "k2": {}})
    before = _seen(world, task_id)

    for answer in answers:
        assert handle_tasks_update({"taskId": task_id, "inputResponses": answer}, world.ctx()) == {}
        if answer is not answers[-1]:
            assert world.store.get(task_id).status is TaskStatus.INPUT_REQUIRED

    record = world.store.get(task_id)
    assert record.status is TaskStatus.WORKING
    assert record.input_responses == {"k1": {"name": "Ada"}, "k2": 2}
    assert record.task.input_requests is None
    assert _seen(world, task_id) == before


def test_a_task_parked_by_hand_keeps_its_arguments_whatever_the_answer_carries() -> None:
    world, task_id = _hand_parked({"k1": {}})
    before = _seen(world, task_id)

    answer = {"k1": _accept(count=99)}
    assert handle_tasks_update({"taskId": task_id, "inputResponses": answer}, world.ctx()) == {}

    assert world.store.get(task_id).arguments == {"count": 5}
    assert _seen(world, task_id) == before


def test_a_task_parked_with_no_requests_is_not_queued_by_an_answer() -> None:
    world, task_id = _hand_parked({})
    before = _seen(world, task_id)

    handle_tasks_update({"taskId": task_id, "inputResponses": {"x": 1}}, world.ctx())

    assert _seen(world, task_id) == before


@pytest.mark.parametrize("shape", ["round-zero", "more-than-the-key", "another-key"])
def test_only_the_question_the_package_asked_resumes_a_task(shape: str) -> None:
    """The package resumes a task whose one outstanding request is the key of its
    recorded round, answered under that key. Each conjunct has its case: a round
    of 0 (whose key is ``additionalInput-0``), a further request beside the key, and
    an answer under another key."""
    if shape == "round-zero":
        world, task_id = _hand_parked({input_request_key(0): {}})
        answer: dict[str, Any] = {input_request_key(0): _accept(confirmed=True)}
    else:
        world = _World()
        task_id = world.start(500)
        key = input_request_key(1)
        if shape == "more-than-the-key":
            record = world.store.get(task_id)
            world.store.save(
                record.with_task(input_requests={**record.task.input_requests, "k2": {}})
            )
            answer = {key: _accept(confirmed=True), "k2": 2}
        else:
            answer = {"made-up": _accept(confirmed=True)}
    before = _seen(world, task_id)

    assert handle_tasks_update({"taskId": task_id, "inputResponses": answer}, world.ctx()) == {}

    assert _seen(world, task_id) == before


def test_a_task_whose_tool_is_no_longer_registered_is_queued_as_it_stands() -> None:
    """Nothing is left to merge into or to charge, so the answer queues the task
    and its worker answers the unknown tool as any run would."""
    world = _World()
    task = create_task(
        store=world.store,
        executor=world.executor,
        tool_name="gone.tool",
        arguments={"count": 5},
        token=TokenInfo(user=None),
        ttl_ms=None,
        poll_interval_ms=None,
        can_elicit=True,
    )
    key = input_request_key(1)
    record = world.store.get(task.task_id)
    world.store.save(replace(record, enqueued=False, input_round=1))
    transition_task(
        world.store, task.task_id, status=TaskStatus.INPUT_REQUIRED, input_requests={key: {}}
    )

    assert world.answer(task.task_id, _accept(confirmed=True), key) == {}

    assert world.store.get(task.task_id).status is TaskStatus.WORKING
    assert world.executor.enqueued == [task.task_id] * 2
    assert world.limiter.charges == 0
    assert world.store.get(task.task_id).arguments == {"count": 5}
