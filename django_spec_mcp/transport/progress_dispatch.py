from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import StreamingHttpResponse
from django_service_specs import (
    DispatchError,
    NotPermitted,
    PrincipalUnavailable,
    ProgressReporter,
    apply_server_arguments,
    check_call,
)

from django_spec_mcp.handlers.kernel_utils import (
    call_context,
    carry_token,
    page_request,
    principal_of,
    unguarded_grant,
)
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import judge_tool_permissions
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.response_stream import build_response_stream
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.progress_token_meta_key import PROGRESS_TOKEN_META_KEY


def progress_token(params: Any) -> str | int | None:
    """The token by which a client asked to hear about this request's progress.

    Era-independent: ``_meta.progressToken`` sits in the same place in
    ``2025-11-25`` and ``2026-07-28``, so the transport does not branch here.

    A non-string, non-integer token is treated as absent rather than rejected.
    The spec constrains the type, but a server **MAY** decline to send progress
    at all, so declining is always legal, while rejecting would fail a request
    over a field that only affects an optional courtesy.
    """
    if not isinstance(params, dict):
        return None
    meta: Any = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    token: Any = meta.get(PROGRESS_TOKEN_META_KEY)
    # ``bool`` is an ``int`` subclass and is plainly not a token.
    if isinstance(token, bool) or not isinstance(token, str | int):
        return None
    return token


def can_report_progress(method: str, params: Any, context: MCPCallContext) -> bool:
    """Whether this request's dispatch can actually emit progress.

    Narrower than "the client asked", and deliberately so. Opening a stream for
    a dispatch that will never report costs a connection, buys nothing, and
    silently gives up the normative ``403``: a ``StreamingHttpResponse`` commits
    its status before the handler runs, and ``preflight_permissions`` can
    only speak for ``tools/call``. So streaming is confined to the paths that
    actually thread a reporter — ``tools/call`` on a service or selector
    binding.

    ``resources/read`` and ``prompts/get`` never receive one. This gate is where
    reporting gets re-enabled once one is threaded through.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return False
    binding = _tool_binding(params, context)
    return binding is not None


def preflight_permissions(method: str, params: Any, context: MCPCallContext) -> JsonRpcError | None:
    """Run a tool's permission stack *before* deciding to stream.

    A ``StreamingHttpResponse`` commits its status before the dispatch runs, so
    a denial discovered inside the handler could only ride as an in-stream
    error inside a ``200`` — losing the ``403`` the MCP authorization spec
    makes normative and the ``WWW-Authenticate`` challenge. Safe to run twice:
    a permission check is a pure predicate over the request, the token and the
    route the call names, and both checks are shown the same route.

    **Permissions only, never rate limits.** Consuming a rate limit is not
    idempotent, so pre-flighting one would charge every streamed request twice,
    and buy nothing — a rate-limit rejection is already a ``200`` with the
    detail in the body.

    Returns ``None`` when there is nothing to deny: only ``tools/call`` has a
    binding to check here. That narrowness is safe **because**
    ``can_report_progress`` refuses to stream anything this cannot speak
    for — changing one without the other reopens the hole.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    binding = _tool_binding(params, context)
    if binding is None:
        return None
    # The call as the handler's own check judges it, through the same helper:
    # a spec permission scoping by ``view.kwargs["project_pk"]`` refused here
    # with a ``403`` the call it was about to admit
    # (``test_the_preflight_sees_the_url_kwargs_the_call_delivers``), and one
    # reading ``request.data`` raised
    # (``test_a_streamed_call_is_judged_on_its_arguments``). Not refusing a
    # missing kwarg, which the handler still names after the permission has
    # answered
    # (``test_a_denied_caller_missing_a_url_kwarg_is_refused_by_the_preflight``).
    # This runs before the handler validates ``arguments``, so one that is not
    # an object delivers nothing here, as an absent one does there, and the
    # handler is left to name the fault
    # (``test_a_call_with_no_arguments_object_is_judged_on_the_route_defaults``).
    # ``url_kwargs`` is read bare because only a service or a selector binding
    # gets here.
    arguments: Any = params.get("arguments")
    sent: dict[str, Any] = arguments if isinstance(arguments, dict) else {}
    allowed, required_scopes = judge_tool_permissions(binding, sent, context)
    if not allowed:
        return JsonRpcError(
            JsonRpcErrorCode.FORBIDDEN,
            "Insufficient permission",
            data={"requiredScopes": required_scopes} if required_scopes else None,
        )
    # One arc to coverage: without the first condition a drf-services binding
    # reaches the kernel's prelude and raises on what only a kernel binding
    # carries (``paginate``, the ``unguarded`` mark)
    # (``test_the_preflight_sees_the_url_kwargs_the_call_delivers``), and
    # without the second no kernel check is pre-flighted
    # (``test_a_spec_check_denying_a_streamed_call_keeps_its_403_and_challenge``).
    if runs_on_kernel(binding) and _kernel_refuses(binding, sent, context):
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
    return None


def _kernel_refuses(binding: Any, arguments: dict[str, Any], context: MCPCallContext) -> bool:
    """Whether the kernel's prelude refuses this call as a permission denial.

    A kernel binding's ``permissions`` hold only its ``scopes=`` gate, which
    ``judge_tool_permissions`` has judged. The spec's own permission checks run
    inside ``adispatch``, after a stream has committed its ``200``, so a
    call they deny streamed ``-32006`` where the same call without a progress
    token was a ``403`` with a challenge
    (``test_a_spec_check_denying_a_streamed_call_keeps_its_403_and_challenge``).

    ``check_call`` is dispatch's own prelude, in dispatch's order, rather than
    ``authorize`` alone: dispatch refuses a deactivated principal first
    (``test_a_deactivated_principal_is_refused_before_the_stream``) and checks
    the arguments before the permissions, so a denied caller sending an
    unknown argument is told about the argument on both paths
    (``test_a_refusal_that_is_not_a_denial_is_left_to_the_handler``, which
    ``authorize`` fails).

    It is handed what ``run_kernel_tool_async`` hands ``adispatch``:

    - the server's arguments laid over the caller's, since the handler lays
      them only after this runs
      (``test_a_check_reading_arguments_is_judged_on_the_servers_value``),
      through the kernel's rule rather than ``apply_tool_server_arguments``,
      which would log the dropped value a second time
      (``test_the_server_arguments_are_laid_without_a_second_warning``);
    - a paged tool's arguments with ``page`` and ``limit`` split off, or the
      kernel refuses them as unknown ahead of the permission check
      (``test_a_paged_call_its_spec_denies_keeps_its_403``);
    - the call context carrying the token
      (``test_a_check_reading_the_token_sees_the_calls_request``);
    - and the grant of an unguarded spec
      (``test_an_unguarded_spec_streams_under_the_grant_its_call_carries``).

    Only the two refusals ``KERNEL_REFUSALS`` answers ``-32006`` deny here. Any
    other is left to the handler, which answers it in the stream as it would
    without one (``test_a_refusal_that_is_not_a_denial_is_left_to_the_handler``).
    Arguments a retry's answers would merge in are not seen here, as the
    drf-services pre-flight does not see them either: the handler judges
    those again.
    """
    principal: Any = principal_of(context.token.user)
    laid: dict[str, Any] = apply_server_arguments(binding.spec, arguments, binding.server_arguments)
    dispatched, _ = page_request(binding, laid, context.config)
    try:
        check_call(
            binding.spec,
            principal=principal,
            arguments=dispatched,
            grant=unguarded_grant(binding, principal, context.config),
            pool_seeds=context.pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            call_context=call_context(
                carry_token(context.http_request, context.token), binding.name
            ),
        )
    except (NotPermitted, PrincipalUnavailable):
        return True
    except DispatchError:
        return False
    return False


def _tool_binding(params: dict[str, Any], context: MCPCallContext) -> Any:
    """The binding a ``tools/call`` names, or ``None`` if it names none.

    Shared so the streaming gate and the permission pre-flight resolve the
    *same* binding: two lookups that disagreed would be the exact bug this
    pairing exists to prevent.
    """
    name: Any = params.get("name")
    if not isinstance(name, str):
        return None
    return context.tools.get(name)


def stream_with_progress(
    *,
    dispatch: Callable[[ProgressReporter], Awaitable[Any]],
    request_id: Any,
    token: str | int,
    context: MCPCallContext,
) -> StreamingHttpResponse:
    """Answer this request with a progress-carrying SSE stream."""
    return build_response_stream(
        dispatch=dispatch,
        request_id=request_id,
        progress_token=token,
        max_notifications=context.config.max_progress_notifications,
    )


__all__ = [
    "can_report_progress",
    "preflight_permissions",
    "progress_token",
    "stream_with_progress",
]
