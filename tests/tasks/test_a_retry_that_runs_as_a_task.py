"""A retry that runs as a task carries its merged ``inputResponses`` into the record.

The worker replays the recorded arguments and nothing else, so answers left out
of the record are answers the task never sees: it ran without them and completed
with an ``isError`` ``input_required`` result. Task creation therefore merges
them as the inline call does, with or without a valid ``requestState``, and
this file holds the merge on all three routes into it: the plain sync handler,
the async one, and a streamed call, whose pre-flight admission is handed in.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django_service_specs import DataclassValidator, PermissionCheck, ServerArguments, ServiceSpec

from django_spec_mcp import MCPServer, TaskPolicy
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.kernel_utils import admit_kernel_tool_call
from django_spec_mcp.handlers.task_dispatch import maybe_create_task
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.task_status import TaskStatus
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from tests.elicitation.conftest import TOOL, DeleteRows, delete_rows_kernel
from tests.tasks.conftest import RecordingExecutor, context

_ROUTES = pytest.mark.parametrize("route", ["sync", "async", "streamed"])
_ANSWER: dict[str, Any] = {
    ELICITATION_KEY: {"action": "accept", "content": {"confirmed": True}},
}
_SENT: dict[str, Any] = {"count": 500}


class _Limiter:
    """Records each charge, admitting every call."""

    def __init__(self) -> None:
        self.charges: int = 0

    def consume(self, request: Any, token: Any) -> int | None:
        self.charges += 1
        return None


class _Asked(PermissionCheck):
    """Admits every caller, recording the arguments it is asked on."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        self.seen.append(dict(arguments))
        return True


class _RefusesAnAnswer(_Asked):
    """Refuses a call that carries ``confirmed``, which only an answer supplies."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        super().has_permission(principal, spec, arguments=arguments)
        return not arguments.get("confirmed")


class _World:
    """A task-running server holding ``rows.delete``, and what its task wrote."""

    def __init__(
        self,
        *,
        check: PermissionCheck | None = None,
        policy: TaskPolicy = TaskPolicy.OPTIONAL,
        **tool: Any,
    ) -> None:
        self.store = InMemoryTaskStore()
        self.executor = RecordingExecutor(self.store)
        self.limiter = _Limiter()
        self.server = MCPServer(
            name="retry",
            auth_backend=AllowAnyBackend(),
            task_store=self.store,
            task_executor=self.executor,
        )
        self.server.register_tool(
            ServiceSpec(
                service=delete_rows_kernel,
                validator=DataclassValidator(DeleteRows),
                permissions=[check or _Asked()],
                atomic=False,
            ),
            name=TOOL,
            description="Delete rows.",
            task_policy=policy,
            rate_limits=[self.limiter],
            **tool,
        )

    def ctx(self, *, task: bool = True) -> MCPCallContext:
        """A request that can be asked, and that declares the tasks extension if ``task``."""
        extensions: dict[str, Any] = {TASKS_EXTENSION_ID: {}} if task else {}
        return replace(
            context(self.server),
            client_capabilities={"elicitation": {}, "extensions": extensions},
        )

    async def call(self, route: str, params: dict[str, Any], ctx: MCPCallContext) -> Any:
        if route == "sync":
            return await sync_to_async(handle_tools_call)(params, ctx)
        if route == "async":
            return await handle_tools_call_async(params, ctx)
        # A streamed call: the pre-flight admits what the client sent, and hands it in.
        binding = self.server.tools.get(TOOL)
        preflight = await sync_to_async(admit_kernel_tool_call)(binding, params["arguments"], ctx)
        assert not isinstance(preflight, JsonRpcError), preflight
        return await handle_tools_call_async(params, ctx, admission=preflight)

    async def round_one_state(self) -> str:
        """The ``requestState`` an inline first round hands back."""
        asked: Any = await sync_to_async(handle_tools_call)(
            {"name": TOOL, "arguments": _SENT}, self.ctx(task=False)
        )
        assert asked["resultType"] == "input_required", asked
        return str(asked["requestState"])

    async def run(self, handle: Any) -> Any:
        """Run the worker for a task handle and return the record's stored state."""
        assert handle["resultType"] == "task", handle
        await sync_to_async(self.server.run_task)(handle["taskId"])
        return self.store.get(handle["taskId"])


def _retry(state: str | None, answer: dict[str, Any] = _ANSWER) -> dict[str, Any]:
    params: dict[str, Any] = {"name": TOOL, "arguments": dict(_SENT), "inputResponses": answer}
    if state is not None:
        params["requestState"] = state
    return params


@_ROUTES
async def test_a_retry_that_runs_as_a_task_stores_and_runs_its_answer(route: str) -> None:
    world = _World()
    state = await world.round_one_state()
    world.limiter.charges = 0

    handle = await world.call(route, _retry(state), world.ctx())

    record = await world.run(handle)
    assert record.arguments == {"count": 500, "confirmed": True}
    assert record.status is TaskStatus.COMPLETED
    assert record.task.result.get("isError", False) is False, record.task.result
    assert record.task.result["structuredContent"]["deleted"] == 500
    assert world.limiter.charges == 1


@_ROUTES
@pytest.mark.parametrize("state", [None, "junk"], ids=["no-state", "junk-state"])
async def test_a_first_call_with_answers_runs_as_a_task_as_inline_does(
    route: str, state: str | None
) -> None:
    """A first call may carry answers, with no state or one that does not verify."""
    world = _World()
    inline: Any = await sync_to_async(handle_tools_call)(_retry(state), world.ctx(task=False))
    assert inline.get("isError", False) is False, inline

    handle = await world.call(route, _retry(state), world.ctx())

    record = await world.run(handle)
    assert record.arguments == {"count": 500, "confirmed": True}
    assert record.task.result["structuredContent"] == inline["structuredContent"]


@_ROUTES
@pytest.mark.parametrize("policy", [TaskPolicy.OPTIONAL, TaskPolicy.REQUIRED])
async def test_either_task_policy_takes_its_answers_into_the_task(
    route: str, policy: TaskPolicy
) -> None:
    world = _World(policy=policy)

    handle = await world.call(route, _retry(None), world.ctx())

    record = await world.run(handle)
    assert record.arguments == {"count": 500, "confirmed": True}
    assert record.task.result.get("isError", False) is False, record.task.result


@_ROUTES
async def test_a_junk_state_with_no_answers_stores_the_arguments_as_sent(route: str) -> None:
    world = _World()
    params: dict[str, Any] = {"name": TOOL, "arguments": dict(_SENT), "requestState": "junk"}

    handle = await world.call(route, params, world.ctx())

    record = await world.run(handle)
    assert record.arguments == {"count": 500}
    # A worker asks nobody: the service's question degrades to an error result.
    assert record.task.result["isError"] is True


@_ROUTES
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_a_declined_answer_creates_no_task_and_is_charged_once(
    route: str, action: str
) -> None:
    world = _World()

    out = await world.call(route, _retry(None, {ELICITATION_KEY: {"action": action}}), world.ctx())

    assert out.get("isError") is True, out
    assert "task" not in out
    assert world.executor.enqueued == []
    assert world.limiter.charges == 1


@_ROUTES
async def test_an_answer_cannot_replace_a_bound_value_in_a_task(route: str) -> None:
    world = _World(server_arguments=ServerArguments(values={"reason": "audit"}))
    answer = {ELICITATION_KEY: {"action": "accept", "content": {"confirmed": True, "reason": "x"}}}

    handle = await world.call(route, _retry(None, answer), world.ctx())

    record = await world.run(handle)
    assert record.arguments == {"count": 500, "confirmed": True, "reason": "audit"}


@_ROUTES
async def test_a_caller_refused_on_the_merged_arguments_gets_no_task(route: str) -> None:
    """The check refuses ``confirmed``, which only the answer supplies: asked on the
    arguments as sent it would admit the call and queue what its worker refuses."""
    world = _World(check=_RefusesAnAnswer())

    out = await world.call(route, _retry(None), world.ctx())

    assert isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN, out
    assert world.executor.enqueued == []
    assert world.limiter.charges == 0


async def test_a_streamed_task_retry_is_admitted_afresh() -> None:
    """The pre-flight asked on the arguments as sent; answers carried in make it a
    different question, so creation asks the checks again, on the merged arguments."""
    check = _Asked()
    world = _World(check=check)

    handle = await world.call("streamed", _retry(None), world.ctx())

    assert handle["resultType"] == "task", handle
    assert check.seen == [{"count": 500}, {"count": 500, "confirmed": True}]


async def test_a_streamed_task_call_with_nothing_carried_is_not_asked_twice() -> None:
    check = _Asked()
    world = _World(check=check)
    params: dict[str, Any] = {"name": TOOL, "arguments": dict(_SENT)}

    handle = await world.call("streamed", params, world.ctx())

    assert handle["resultType"] == "task", handle
    assert check.seen == [{"count": 500}]


async def test_a_pre_flight_admission_stands_when_no_call_is_handed_in() -> None:
    """A direct call has no ``params`` to merge, and an admission it was given stands."""
    check = _Asked()
    world = _World(check=check)
    ctx = world.ctx()
    binding = world.server.tools.get(TOOL)
    preflight = await sync_to_async(admit_kernel_tool_call)(binding, dict(_SENT), ctx)
    assert not isinstance(preflight, JsonRpcError), preflight

    out = await sync_to_async(maybe_create_task)(binding, dict(_SENT), ctx, admission=preflight)

    assert out is not None and out["resultType"] == "task", out
    assert check.seen == [{"count": 500}]
