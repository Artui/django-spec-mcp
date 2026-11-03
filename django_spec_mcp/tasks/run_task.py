from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.elicitation.fingerprint_request import fingerprint_request
from django_spec_mcp.elicitation.sign_request_state import sign_request_state
from django_spec_mcp.elicitation.types.request_state import RequestState
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.input_dispatch import TOOLS_CALL_METHOD
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from django_spec_mcp.tasks.report_task_progress import report_task_progress
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import input_request_key
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.result_type import ResultType
from django_spec_mcp.types.task_status import TaskStatus


def run_task(
    store: TaskStore,
    task_id: str,
    *,
    context_factory: Callable[[TaskRecord], MCPCallContext],
) -> None:
    """Execute a stored task and record its outcome. Called **from the worker**.

    The whole of the off-request half. A queue consumer knows only an id;
    everything else — the call, the principal, the scopes — comes back out of
    the store, and the outcome goes back into it. Nothing is returned: the
    client learns the result by polling.

    **Claim before running.** Queues deliver at least once, and running a
    mutation twice because the broker retried would be a data bug with no trace.
    A task is claimed by clearing its ``enqueued`` flag, and one that is not
    claimable — unknown, already claimed, already terminal — is a no-op. This is
    best-effort, not a lock: no backend here does compare-and-swap, so two
    workers entering in the same instant can both claim. It closes the ordinary
    duplicate-delivery case; a consumer that cannot tolerate the residual race
    wants an idempotent service.

    **Permissions run again here, in full.** The stored scopes and user rebuild
    the same ``TokenInfo`` the request carried, so the binding's permissions and
    its object-permission hook run exactly as they did inline. That is why the
    record stores the authorization context rather than a bare principal id: a
    re-check with a token that proves nothing is not a re-check.

    **Rate limits are the one guard that does not run again**, and deliberately:
    they are consumed rather than tested, and the request that asked for the
    task already paid (see ``maybe_create_task``). The worker's context carries
    ``enforce_rate_limits=False`` so a replay cannot bill the same client call
    twice.

    A denial or any other JSON-RPC error becomes a ``failed`` task — those are
    protocol faults with no protocol response left to carry them. A tool-level
    failure is *not*: it arrives as an ordinary result carrying ``isError:
    true`` and the task is ``completed``, as the spec requires.

    **A service that asks parks the task.** When the creating request declared
    form elicitation (``TaskRecord.can_elicit``), the replay runs under that
    declaration, so ``AdditionalInputRequired`` comes back as the inline
    ``input_required`` result rather than an ``isError`` one. The task then moves
    to ``input_required`` with that result's question under a key of its own
    (``input_request_key``), and waits, unclaimed, for ``tasks/update`` to merge
    the answer into its arguments and queue it again. Each run after an answer
    is the next round, carried in a ``requestState`` signed here for this replay
    alone, so the inline round budget (``MAX_INPUT_ROUNDS``) bounds the exchange
    and its last question becomes the inline ``isError`` result.
    """
    record: TaskRecord | None = store.get(task_id)
    if record is None or not record.enqueued or record.status.is_terminal:
        return
    store.save(replace(record, enqueued=False))

    # The registries, config and identity live on the server and the token on
    # the record; ``MCPServer`` closes over the first half and this supplies the
    # second, which keeps this module free of any import from ``server/``.
    context: MCPCallContext = context_factory(record)
    # ``progress`` is seeded here rather than by ``context_factory`` because only
    # this function knows the task id that makes it addressable. Without it a
    # service's ``progress(...)`` calls resolve to the no-op.
    context = replace(context, progress=report_task_progress(store, task_id))
    # The broker travels on the context so the worker publishes to the same
    # server's subscribers — there is no request here to carry it.
    broker = context.subscriptions
    if record.can_elicit:
        # The creating request's declaration, which is all ``ask_for_input``
        # reads: a worker serves no client, so its context declares nothing.
        context = replace(context, client_capabilities={"elicitation": {}})
    try:
        result: Any = handle_tools_call(_replay(record, context), context)
    except Exception as exc:  # noqa: BLE001 — the worker's last line of defence
        # Without this the exception propagates into the queue consumer, the
        # task stays ``working``, and the client polls a handle that resolves
        # only at its TTL. A visible ``failed`` beats a silent hang.
        transition_task(
            store,
            task_id,
            status=TaskStatus.FAILED,
            status_message=f"The task raised an unhandled exception: {exc}",
            broker=broker,
        )
        raise

    if isinstance(result, JsonRpcError):
        transition_task(
            store,
            task_id,
            status=TaskStatus.FAILED,
            status_message=result.message,
            error=result.to_dict(),
            broker=broker,
        )
        return
    if result.get("resultType") == ResultType.INPUT_REQUIRED.value:
        _park(store, task_id, result, broker)
        return
    transition_task(store, task_id, status=TaskStatus.COMPLETED, result=result, broker=broker)


def _replay(record: TaskRecord, context: MCPCallContext) -> dict[str, Any]:
    """The ``tools/call`` a run replays: the recorded call, and past a first
    question the round it is on.

    The round reaches the budget check the only way the inline call's does, in a
    ``requestState``, signed here for this replay rather than stored: a stored one
    would expire with ``INPUT_REQUEST_TTL_SECONDS`` while the task waited. It
    carries no answers, since ``tasks/update`` merged them into the arguments, and
    binds the arguments as recorded, which the replay presents again.
    """
    params: dict[str, Any] = {"name": record.tool_name, "arguments": record.arguments}
    if record.input_round:
        state = RequestState(
            principal=principal_for_token(context.token),
            fingerprint=fingerprint_request(
                TOOLS_CALL_METHOD, {"name": record.tool_name, "arguments": record.arguments}
            ),
            round=record.input_round,
        )
        params["requestState"] = sign_request_state(state)
    return params


def _park(
    store: TaskStore,
    task_id: str,
    result: dict[str, Any],
    broker: SubscriptionBroker | None,
) -> None:
    """Move a task whose service asked to ``input_required``, its question under
    the next round's key.

    Re-read rather than taken from the claim, so progress the run reported is
    kept. The round is saved before the status moves because the reverse order
    would save a record read before the move over ``input_required``, leaving the
    task ``working`` with its question lost. A ``tasks/update`` landing between
    the two saves is refused, since the status is still ``working``. A task
    cancelled while it ran stays cancelled (``transition_task``), and one
    cancelled between the last read and the save is kept too, by reading once
    more directly before the save
    (``test_a_cancel_between_the_parks_read_and_its_save_is_kept``): still two
    calls, the window ``transition_task`` accepts.
    """
    current: TaskRecord | None = store.get(task_id)
    if current is None or current.status.is_terminal:
        return
    # ``ask_for_input`` asked this run's question as the round after the one
    # the replay carried.
    asked: int = current.input_round + 1
    latest: TaskRecord | None = store.get(task_id)
    if latest is None or latest.status.is_terminal:
        return
    store.save(replace(latest, input_round=asked))
    transition_task(
        store,
        task_id,
        status=TaskStatus.INPUT_REQUIRED,
        input_requests={input_request_key(asked): result["inputRequests"][ELICITATION_KEY]},
        broker=broker,
    )


__all__ = ["run_task"]
