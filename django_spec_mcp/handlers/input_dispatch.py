from __future__ import annotations

from collections.abc import Collection
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import AdditionalInputRequired

from django_spec_mcp.auth.principal_for_token import principal_for_token
from django_spec_mcp.elicitation.build_requested_schema import build_requested_schema
from django_spec_mcp.elicitation.can_ask_client import can_ask_client
from django_spec_mcp.elicitation.fingerprint_request import fingerprint_request
from django_spec_mcp.elicitation.read_elicit_answer import read_elicit_answer
from django_spec_mcp.elicitation.sign_request_state import sign_request_state
from django_spec_mcp.elicitation.types.elicit_answer import ElicitAnswer
from django_spec_mcp.elicitation.types.elicit_request import ElicitRequest
from django_spec_mcp.elicitation.types.input_required_result import InputRequiredResult
from django_spec_mcp.elicitation.types.request_state import RequestState
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.elicitation.verify_request_state import verify_request_state
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.elicit_action import ElicitAction
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode

TOOLS_CALL_METHOD: str = "tools/call"


def resolve_prior_input(
    params: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    context: MCPCallContext,
) -> ResolvedInput:
    """Fold any earlier round's answers into the arguments this call runs with."""
    fingerprint: str = fingerprint_request(
        TOOLS_CALL_METHOD, {"name": tool_name, "arguments": arguments}
    )
    state: RequestState | None = verify_request_state(
        params.get("requestState"),
        principal=principal_for_token(context.token),
        fingerprint=fingerprint,
        max_age=context.config.input_request_ttl_seconds,
    )
    answer: ElicitAnswer | None = read_elicit_answer(params)

    if answer is not None and answer.action is not ElicitAction.ACCEPT:
        return ResolvedInput(
            arguments=arguments,
            fingerprint=fingerprint,
            round=state.round if state else 0,
            refused_with=answer.action,
        )

    carried: dict[str, Any] = dict(state.answers) if state else {}
    if answer is not None:
        carried.update(answer.content)
    return ResolvedInput(
        arguments={**arguments, **carried},
        fingerprint=fingerprint,
        round=state.round if state else 0,
        carried=carried,
    )


def ask_for_input(
    exc: AdditionalInputRequired,
    prior: ResolvedInput,
    context: MCPCallContext,
    *,
    admitted: Collection[str],
) -> dict[str, Any] | JsonRpcError:
    """Turn a service's *"I need something else"* into whatever this client can use."""
    if not exc.schema:
        return _degraded(exc)

    try:
        requested_schema: dict[str, Any] = build_requested_schema(exc.schema, admitted=admitted)
    except ImproperlyConfigured as bad_schema:
        return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(bad_schema))

    if not can_ask_client(context.client_capabilities):
        return _degraded(exc)
    if prior.round >= context.config.max_input_rounds:
        return _degraded(exc)

    state = RequestState(
        principal=principal_for_token(context.token),
        fingerprint=prior.fingerprint,
        answers=prior.carried,
        round=prior.round + 1,
    )
    return InputRequiredResult(
        input_requests={ELICITATION_KEY: ElicitRequest(exc.message, requested_schema)},
        request_state=sign_request_state(state),
    ).to_dict()


def refusal_result(action: ElicitAction) -> dict[str, Any]:
    """The tool result for a user who was asked and did not answer."""
    declined: bool = action is ElicitAction.DECLINE
    message: str = (
        "The user declined to provide the input this tool asked for."
        if declined
        else "The user dismissed the request for input without answering."
    )
    return build_error_tool_result(
        message,
        error_type="input_declined" if declined else "input_cancelled",
        detail={"action": action.value},
    ).to_dict()


def _degraded(exc: AdditionalInputRequired) -> dict[str, Any]:
    """The service's own message, plus the shape of what it wanted, as an error."""
    return build_error_tool_result(
        exc.message,
        error_type="input_required",
        detail={"requestedInput": dict(exc.schema)} if exc.schema else None,
    ).to_dict()


__all__ = ["TOOLS_CALL_METHOD", "ask_for_input", "refusal_result", "resolve_prior_input"]
