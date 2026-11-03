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
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_tasks_cancel import handle_tasks_cancel
from django_spec_mcp.handlers.handle_tasks_get import handle_tasks_get
from django_spec_mcp.handlers.handle_tasks_update import handle_tasks_update
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
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
