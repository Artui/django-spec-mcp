from __future__ import annotations

from dataclasses import replace
from typing import Any

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.admit_task_call import admit_task_call
from django_spec_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
    owned_task,
    resolve_task_id,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import span_attrs
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.subscriptions.publish_after_task import publish_task_status
from django_spec_mcp.tasks.resume_task import resume_task
from django_spec_mcp.tasks.transition_task import transition_task
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.types.task_store import TaskStore
from django_spec_mcp.tasks.utils import input_request_key, now_iso
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.task_status import TaskStatus


def handle_tasks_update(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Deliver the client's answers to a task's outstanding input requests.

    The half of the extension that makes a task interactive: a worker needing
    something only the client can supply parks the task in ``input_required``
    with keyed ``inputRequests``; the client answers them here.

    Three rules from the spec, all about being forgiving in the right places:

    - **Unknown keys are ignored, not rejected.** A client that polls twice can
      answer the same request twice, and an invented key is not worth failing
      the whole call over.
    - **A partial set is accepted**, so answers accumulate rather than replace.
    - **Only when nothing is left outstanding** does the task go back to
      ``working`` (queued again for its worker, if the package parked it); until
      then the next ``tasks/get`` shows what is still wanted.

    **The answer resumes a task the package parked as a retry would resume the
    call.** A task whose one outstanding request is the key of its recorded round
    (``run_task`` parked it) is resumed: the answer merges into the task's
    arguments, under the checks and the charge task creation asks (``_resume``),
    and the worker runs the service again from the top with it, as an inline retry
    does. A task parked through ``transition_task`` under its caller's own keys is
    answered as it was in 0.6.0: the answers are kept, and the task is ``working``
    again once none is outstanding, with nothing queued, charged or merged.

    **Updating a task that is not waiting for input is refused.** The keys are
    the correlation, and a task in ``working`` has none outstanding, so every
    answer would be an unknown key: silently ignored, acknowledged as success,
    and indistinguishable from a delivery that worked.

    **Answers arrive from the client and are model- or user-authored.** The spec
    requires the same trust model as an elicitation response, which is none:
    nothing here interprets a value, and a worker reading them owes them the
    validation it would give any request body.
    """
    if not declares_tasks_extension(context):
        return missing_capability_error()
    task_id: str | JsonRpcError = resolve_task_id(params)
    if isinstance(task_id, JsonRpcError):
        return task_id
    assert isinstance(params, dict)  # noqa: S101 — resolve_task_id proved it

    responses: Any = params.get("inputResponses")
    if not isinstance(responses, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "'inputResponses' is required and must be an object keyed by request id",
        )

    owned = owned_task(task_id, context)
    if isinstance(owned, JsonRpcError):
        return owned
    store, record = owned

    if record.status is not TaskStatus.INPUT_REQUIRED:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            f"Task {task_id!r} is not awaiting input (status: {record.status.value}).",
        )

    outstanding: dict[str, Any] = record.task.input_requests or {}
    accepted: dict[str, Any] = {k: v for k, v in responses.items() if k in outstanding}
    merged: dict[str, Any] = {**record.input_responses, **accepted}
    remaining: dict[str, Any] = {k: v for k, v in outstanding.items() if k not in merged}

    asked: str = input_request_key(record.input_round)
    # A task this package parked (``run_task``) waits on exactly its round's one
    # key, and its answer lands there. Each conjunct has the test that fails
    # without it (``test_only_the_question_the_package_asked_resumes_a_task``):
    # ``input_round >= 1`` (a hand-parked task keyed ``additionalInput-0``), the
    # outstanding set being only that key (a further request beside it), and the
    # key being answered (an answer under another key resumes nothing).
    if record.input_round >= 1 and set(outstanding) == {asked} and asked in accepted:
        return _resume(store, replace(record, input_responses=merged), accepted, context)

    # Any other task in ``input_required`` was parked through ``transition_task``
    # under its caller's own keys, and is answered as it was before the package
    # parked tasks of its own: the answers are kept, and once none is outstanding
    # the task is ``working`` again with nothing queued, charged or merged
    # (``test_a_task_parked_by_hand_is_answered_as_in_0_6_0``).
    updated: TaskRecord = replace(record, input_responses=merged).with_task(
        status=TaskStatus.WORKING if not remaining else TaskStatus.INPUT_REQUIRED,
        input_requests=remaining or None,
        last_updated_at=now_iso(),
        status_message=None if not remaining else record.task.status_message,
    )
    store.save(updated)
    # Answering changes the task's status, so a subscriber watching it hears,
    # exactly as it would for any other transition.
    publish_task_status(context.subscriptions, updated)
    return {}


def _resume(
    store: TaskStore,
    record: TaskRecord,
    answered: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Merge the answer into the task's arguments and queue it again.

    The answer is handed to ``admit_task_call`` as a retry's ``inputResponses``,
    under the key the inline exchange files it under, with the recorded arguments
    as the call's: so it merges as an answer carried into task creation does, the
    binding's ``server_arguments`` laid over it, and the scope gate, the spec's
    checks on the merged arguments and the charge are asked of this request as
    they were of the one that created the task. The worker charges nothing, so
    this is where the round the client answers pays, as an inline retry does
    (``test_each_answering_round_is_charged_once``).

    A denial or a refused charge is this request's answer, and the task keeps
    waiting with nothing of the answer kept, so the client can answer again
    (``test_a_refused_charge_leaves_the_task_waiting``). A declined or cancelled
    answer ends the task with the inline refusal as its result. A tool no longer
    registered merges nothing: the task is queued as it stands, and its worker
    answers the unknown tool as any run would.
    """
    binding: Any = context.tools.get(record.tool_name)
    arguments: dict[str, Any] = record.arguments
    if binding is not None:
        # The one key a parked task waits on (``run_task``); its answer is the
        # inline exchange's, so it is read under the inline key.
        answer: Any = next(iter(answered.values()), None)
        params: dict[str, Any] = {"inputResponses": {ELICITATION_KEY: answer}}
        # Inside the span task creation opens, for the same checks and the same
        # refusals (``test_an_answer_refused_on_a_waiting_task_is_traced``).
        with span("mcp.tools.call", attributes=span_attrs(binding.name, context)):
            refusal, arguments = admit_task_call(binding, arguments, context, params=params)
        if isinstance(refusal, JsonRpcError):
            return refusal
        if refusal is not None:
            transition_task(
                store,
                record.task_id,
                status=TaskStatus.COMPLETED,
                result=refusal,
                broker=context.subscriptions,
            )
            return {}
    resume_task(
        store,
        context.task_executor,
        replace(record, arguments=arguments),
        broker=context.subscriptions,
    )
    return {}


__all__ = ["handle_tasks_update"]
