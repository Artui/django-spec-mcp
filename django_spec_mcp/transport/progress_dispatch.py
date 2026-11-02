from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import StreamingHttpResponse
from django_service_specs import ProgressReporter, apply_server_arguments

from django_spec_mcp.handlers.kernel_utils import KernelAdmission, admit_kernel_tool_call
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import forbidden, judge_tool_permissions
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
    """The denial ``preflight_admission`` answers, or ``None`` when there is none.

    For a caller that wants only the question, not the admission that answers
    it for a call that is not refused.
    """
    outcome = preflight_admission(method, params, context)
    return outcome if isinstance(outcome, JsonRpcError) else None


def preflight_admission(
    method: str, params: Any, context: MCPCallContext
) -> JsonRpcError | KernelAdmission | None:
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

    Returns the denial, or the ``KernelAdmission`` the spec's checks made for a
    call they admit, which the streamed dispatch runs under rather than asking
    them again (``test_a_streamed_call_resolves_a_checks_seed_once``). ``None``
    when there is nothing to deny and nothing admitted: only ``tools/call`` has
    a binding to check here. That narrowness is safe **because**
    ``can_report_progress`` refuses to stream anything this cannot speak
    for — changing one without the other reopens the hole.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    binding = _tool_binding(params, context)
    if binding is None:
        return None
    # The binding's scope gate, through the helper the handler's own check
    # uses, then the spec's checks. This runs before the handler validates
    # ``arguments``, so one that is not an object delivers nothing here, as an
    # absent one does there, and the handler is left to name the fault
    # (``test_a_streamed_call_with_no_arguments_object_is_judged_on_none_sent``).
    allowed, required_scopes = judge_tool_permissions(binding, context)
    if not allowed:
        return forbidden(required_scopes)
    arguments: Any = params.get("arguments")
    sent: dict[str, Any] = arguments if isinstance(arguments, dict) else {}
    admitted = _kernel_admission(binding, sent, context)
    if isinstance(admitted, JsonRpcError):
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
    return admitted


def _kernel_admission(
    binding: Any, arguments: dict[str, Any], context: MCPCallContext
) -> KernelAdmission | JsonRpcError:
    """The kernel spec's own checks on this call: its admission, or the denial.

    A kernel binding's ``permissions`` hold only its ``scopes=`` gate, which
    ``judge_tool_permissions`` has judged. The handler asks the spec's checks
    before it charges the call, but inside the stream, after it has committed
    its ``200``, so left to the handler a call they deny would stream
    ``-32006`` where the same call without a progress token is a ``403`` with
    a challenge
    (``test_a_spec_check_denying_a_streamed_call_keeps_its_403_and_challenge``).

    Asked through ``admit_kernel_tool_call``, the step the handler asks them
    in, so the two paths ask them alike: a deactivated principal is refused
    (``test_a_deactivated_principal_is_refused_before_the_stream``), and a
    caller they deny is refused before its arguments are checked, so it is
    not told about an unknown argument it sent on either path
    (``test_a_caller_the_spec_denies_is_refused_before_its_unknown_argument``).
    That step splits a paged tool's ``page`` and ``limit`` off, so an admitted
    paged call is asked on its arguments rather than judged on none
    (``test_a_paged_call_is_asked_once``); hands the checks the
    call context carrying the token
    (``test_a_check_reading_the_token_sees_the_calls_request``), the grant of
    an unguarded spec
    (``test_an_unguarded_spec_streams_under_the_grant_its_call_carries``) and
    the server's own ``pool_seeds``
    (``test_a_check_reading_a_servers_own_seed_is_judged_on_that_seed``); and
    checks the arguments under the binding's own ``unknown_arguments`` policy,
    since a tool that ignores strays shows its checks the arguments without
    them (``test_a_stray_argument_a_tool_ignores_is_asked_once``).

    Handed the server's arguments laid over the caller's, since the handler
    lays them only after this runs
    (``test_a_check_reading_arguments_is_judged_on_the_servers_value``),
    through the kernel's rule rather than ``apply_tool_server_arguments``,
    which would log the dropped value a second time
    (``test_the_server_arguments_are_laid_without_a_second_warning``).

    Only a denial, which ``KERNEL_REFUSALS`` answers ``-32006``, is answered
    here. Any other refusal is left to the handler, which answers it in the
    stream as it would without one
    (``test_a_refusal_that_is_not_a_denial_is_left_to_the_handler``).
    Arguments a retry's answers would merge in are not seen here, so the
    handler reuses this admission only when nothing was merged
    (``test_a_streamed_retry_with_merged_answers_is_admitted_afresh``) and
    otherwise judges them again. A reused admission is the one the checks and the
    run share, so a call whose arguments pass the shape check is asked once,
    here, and ``adispatch`` runs under its grant, its seeds and its call context
    (``test_a_streamed_call_asks_the_spec_as_often_as_a_plain_one``,
    ``test_a_streamed_call_resolves_a_checks_seed_once``). A call failing the
    shape check is asked here as ``admit_kernel_call`` counts, so an admitted
    ``many`` call with a refused row is asked twice streamed, once here and once
    in the run
    (``test_a_many_call_with_a_refused_row_is_asked_by_its_prelude_and_by_dispatch``).
    """
    laid: dict[str, Any] = apply_server_arguments(binding.spec, arguments, binding.server_arguments)
    return admit_kernel_tool_call(binding, laid, context)


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
