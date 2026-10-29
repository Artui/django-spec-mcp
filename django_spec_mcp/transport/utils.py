"""Shared helpers for the sync and async streamable-HTTP views."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, JsonResponse
from django.views import View

from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.kernel_utils import carry_token, seat
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.mcp_error_header import MCP_ERROR_HEADER
from django_spec_mcp.types.session_missing_hint import SESSION_MISSING_HINT
from django_spec_mcp.types.session_unknown_hint import SESSION_UNKNOWN_HINT

NOT_ACCEPTABLE_DETAIL: str = "Could not satisfy the request Accept header."
"""The ``406`` body's ``detail``, worded as the endpoints have always answered it."""


def not_acceptable(request: HttpRequest, media_types: tuple[str, ...]) -> JsonResponse | None:
    """The ``406`` for an ``Accept`` header admitting none of ``media_types``, else ``None``.

    ``media_types`` is what the endpoint can answer with. A request with no
    ``Accept`` header accepts anything, and an empty one accepts nothing, which
    is how Django's ``HttpRequest.accepts`` reads them. Asked before the method
    is routed, so an ``OPTIONS`` or a method the endpoint does not serve is
    refused the same way, as the endpoints have always done
    (``test_an_accept_admitting_nothing_the_endpoint_serves_is_a_406``).
    """
    if any(request.accepts(media_type) for media_type in media_types):
        return None
    return JsonResponse({"detail": NOT_ACCEPTABLE_DETAIL}, status=406)


def allowed_methods(view: View) -> list[str]:
    """The methods ``view`` answers, upper-cased, for an ``Allow`` header.

    Read off the instance, because Django's ``View.setup`` gives ``head`` the
    ``get`` handler there; it is the list Django's own ``405`` and ``OPTIONS``
    answers name, so every ``Allow`` the view sends agrees
    (``test_a_delete_with_sessions_off_is_a_405_naming_the_methods``).
    """
    return [method.upper() for method in view.http_method_names if hasattr(view, method)]


def exempt_endpoint(view: Callable[..., Any]) -> Any:
    """Mark a view function as opting out of CSRF and of ``LoginRequiredMiddleware``.

    The endpoints authenticate with a bearer token through ``MCPAuthBackend``,
    so a CSRF token or a login redirect is something no MCP client can satisfy
    (``test_a_session_cookie_does_not_bring_csrf_to_the_endpoint``,
    ``test_login_required_middleware_never_redirects_an_endpoint``). Set as
    attributes rather than through ``csrf_exempt`` and ``login_not_required``:
    on Django 4.2 ``csrf_exempt`` wraps an async view in a sync function, and
    ``login_not_required`` does not exist before 5.1. The middleware reads
    both off the resolved callable.
    """
    view.csrf_exempt = True  # ty: ignore[unresolved-attribute]
    view.login_required = False  # ty: ignore[unresolved-attribute]
    return view


def carry_principal(request: HttpRequest, token: TokenInfo) -> None:
    """Put the token on ``request`` as ``mcp_token``, and its principal as the user.

    Called the moment a request authenticates, so everything judged on this
    request afterwards (a check gating a prompt, a streamed call's
    pre-flight, a handler) judges the bearer token's principal, never
    the user ``AuthenticationMiddleware`` read off a session cookie
    (``test_a_permission_sees_the_tokens_principal_not_the_sessions``), and an
    ``AnonymousUser`` when the token names no one
    (``test_a_token_naming_no_one_leaves_an_anonymous_user_not_the_sessions``),
    on a request no handler reads as on one a handler does
    (``test_a_request_no_handler_reads_still_carries_the_tokens_principal``).
    It is the handlers' ``carry_token``, called earlier, so a handler writing
    the same values again changes nothing.
    """
    carry_token(request, token)


def seat_anonymous(request: HttpRequest) -> None:
    """Clear whatever user the middleware resolved, before the auth backend runs.

    The endpoints are CSRF-exempt, so a backend taking a session cookie's
    user for the caller (``AllowAnyBackend`` reads ``request.user``) would let
    any page the user visits call tools as them. The backend sees an
    anonymous request, as it did when DRF's empty ``authentication_classes``
    wrote ``AnonymousUser`` there
    (``test_a_session_user_is_not_the_caller_the_auth_backend_sees``).
    """
    seat(request, AnonymousUser())


def is_raised_denial(exc: BaseException) -> bool:
    """Whether an exception escaping dispatch is a permission denial, not a fault.

    Django's ``PermissionDenied`` is one
    (``test_a_raised_permission_denial_is_a_denial_not_a_fault`` in
    ``test_dispatch_backstop.py``), and only it: another library's class named
    ``PermissionDenied`` is a fault like any other exception
    (the ``namesake`` case of
    ``test_an_exception_declaring_a_403_is_still_a_server_fault``).

    **Not by the status an exception declares.** An HTTP client's error for an
    upstream ``403`` carries ``status_code = 403`` too (``openai`` and
    ``anthropic`` both do), and a tool whose upstream key was rejected is a
    server fault, answered ``500`` and logged at ``ERROR``
    (``test_an_exception_declaring_a_403_is_still_a_server_fault``).
    """
    return isinstance(exc, PermissionDenied)


def is_permission_denial(result: Any) -> bool:
    """Whether a dispatch result is a permission denial needing HTTP 403.

    The MCP authorization spec's error table is normative: ``401`` for
    "authorization required or token invalid", ``403`` for "invalid scopes or
    insufficient permissions". A denial is therefore not a plain JSON-RPC error
    riding inside a ``200`` — the status carries meaning a client acts on.
    """
    return isinstance(result, JsonRpcError) and result.code == JsonRpcErrorCode.FORBIDDEN


def raised_denial_error() -> JsonRpcError:
    """The ``FORBIDDEN`` a handler returns for a denial, for one that was raised.

    Both views' backstop catches a raised denial (``is_raised_denial``) ahead
    of its catch-all and answers with this. Every handler already maps a
    ``PermissionDenied`` it catches to ``FORBIDDEN``; what reaches the backstop
    is one raised where no handler is looking, above all by a permission that
    raises instead of returning ``False``. The catch-all would turn it into a
    ``500`` and an ``ERROR`` log, reporting a refusal as a server fault.

    Handed back into the view's normal result path rather than answered on the
    spot, so a raised denial gets exactly what a returned one gets: the era's
    status and the ``WWW-Authenticate`` challenge. The exception's own message is
    not echoed, matching the handlers, whose ``except PermissionDenied`` arms
    answer "Insufficient permission" whatever it said.
    """
    return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")


def insufficient_scope_challenge(result: JsonRpcError, backend: MCPAuthBackend) -> str:
    """Build the ``WWW-Authenticate`` challenge for a permission denial.

    RFC 6750 §3.1 defines ``insufficient_scope`` plus a ``scope`` parameter
    listing what would satisfy the request, which is how a client knows what to
    ask for on the next authorization round.

    A denial with no scopes attached — a ``PermissionCheck`` refusing, on a
    prompt or a view or a spec — still gets a 403, but no ``error=`` / ``scope=``:
    RFC 6750 defines neither for that case, and inventing a scope the client
    cannot obtain would send it round a pointless loop.
    """
    data: Any = result.data if isinstance(result.data, dict) else {}
    scopes: list[str] | None = data.get("requiredScopes") or None
    return backend.www_authenticate_challenge(
        scopes=scopes, error="insufficient_scope" if scopes else None
    )


def modern_error_status(error: JsonRpcError) -> int:
    """HTTP status for a JSON-RPC error on the modern transport.

    The modern revision makes several statuses normative, each carrying
    information the JSON-RPC code alone does not:

    - ``404`` for an unknown method, which is what lets a client tell a modern
      MCP endpoint from a legacy HTTP+SSE server that hosts none: both answer
      ``404``, but only ours carries a ``-32601`` body, and the spec's fallback
      algorithm reads exactly that.
    - ``400`` for the three spec-reserved rejections (header mismatch,
      unsupported version, missing client capability). A client inspects the
      body before falling back to ``initialize``: a recognised modern error
      means "fix the request", anything else means "this server is legacy".
    - ``403`` for a permission denial, as in the legacy era.

    Everything else rides inside a ``200``, which is ordinary JSON-RPC.
    """
    if error.code == JsonRpcErrorCode.METHOD_NOT_FOUND:
        return 404
    if error.code in _MODERN_BAD_REQUEST_CODES:
        return 400
    if error.code == JsonRpcErrorCode.FORBIDDEN:
        return 403
    return 200


_MODERN_BAD_REQUEST_CODES: frozenset[int] = frozenset(
    {
        JsonRpcErrorCode.HEADER_MISMATCH,
        JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
        JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY,
    }
)


def session_gate_failure(
    session_id: str | None, *, owner_matches: bool
) -> tuple[str, int, str] | None:
    """Decide the legacy session gate's outcome. ``None`` means the request passes.

    Returns ``(message, status, hint)``, shared by the sync and async views
    so the two cannot drift. ``hint`` is the ``MCP-Error`` slug.

    **The two statuses are not interchangeable.** A request *without* an
    ``Mcp-Session-Id`` header "SHOULD" get ``400``, while ``404`` is specified
    for a request *"containing that session ID"* after the server has dropped
    it — and ``2025-11-25`` lists ``400`` among the statuses that send a client
    down the legacy-fallback path, so the wrong code lands it in the wrong
    branch. Splitting them leaks nothing: the caller already knows whether it
    sent a header.

    The pair that must stay **merged** is *unknown id* versus *id owned by
    another principal*: those are facts about someone else's session, so both
    render ``404`` with one message and the gate is not an ownership oracle.
    """
    if not session_id:
        return ("Missing MCP-Session-Id", 400, SESSION_MISSING_HINT)
    if not owner_matches:
        return ("Unknown or invalid MCP-Session-Id", 404, SESSION_UNKNOWN_HINT)
    return None


def error_response(
    *,
    code: int,
    message: str,
    data: Any = None,
    status: int = 400,
    request_id: Any = None,
    error_hint: str | None = None,
) -> JsonResponse:
    """A JSON-RPC error envelope under ``status``, the shape every refusal takes."""
    body: dict[str, Any] = JsonRpcResponse(
        id=request_id, error=JsonRpcError(code=code, message=message, data=data)
    ).to_dict()
    response = JsonResponse(body, status=status)
    if error_hint is not None:
        # Summarises the body for clients that surface only the status line.
        response[MCP_ERROR_HEADER] = error_hint
    return response


def internal_error_response(message: JsonRpcRequest, logger: logging.Logger) -> JsonResponse:
    """Answer a dispatch that raised with a JSON-RPC ``-32603``, and log why.

    **The backstop, not a classifier.** Every failure a caller can act on is
    already *returned* by the handlers, as an ``isError`` tool result or a
    JSON-RPC error, and the permission denial, unknown method and rate limit
    responses are all built from those return values. What reaches this is
    what nothing mapped, which makes it a server fault whatever its type.

    Without it the exception would leave the view as Django's ``500`` page,
    which is not a JSON-RPC reply at all: a spec-following client could not
    even match it to the request it sent.

    **Nothing is meant to escape.** The ``406`` for an ``Accept`` header the
    view cannot answer is decided in the view's ``dispatch``, before any
    action is called, so the backstop cannot reach it.
    tests/transport/test_dispatch_backstop.py pins the denial, rate-limit and
    unknown-method answers the handlers return rather than raise.

    **The status stays a ``500``** so proxies, load balancers and error-rate
    monitoring still count a server fault, which a ``200`` carrying the error
    would hide from all three. **The message is generic** and the exception
    goes to the log with its traceback: see ``INTERNAL_ERROR_MESSAGE``.

    ``logger`` is the calling view's own, so an operator's ``LOGGING`` keeps
    telling the two transports apart. Must be called from inside the
    ``except`` block: ``logger.exception`` reads the exception being handled.
    """
    logger.exception(
        "Unhandled exception dispatching %s (request id %r)", message.method, message.id
    )
    return error_response(
        code=JsonRpcErrorCode.INTERNAL_ERROR,
        message=INTERNAL_ERROR_MESSAGE,
        status=500,
        request_id=message.id,
    )


def params_dict(params: Any) -> dict[str, Any] | None:
    """A message's ``params`` when it is an object, else ``None``.

    JSON-RPC also allows positional (list) params, which MCP does not use, so
    they read as no params at all.
    """
    if isinstance(params, dict):
        return params
    return None


__all__ = [
    "NOT_ACCEPTABLE_DETAIL",
    "allowed_methods",
    "carry_principal",
    "error_response",
    "exempt_endpoint",
    "insufficient_scope_challenge",
    "internal_error_response",
    "is_permission_denial",
    "is_raised_denial",
    "modern_error_status",
    "not_acceptable",
    "params_dict",
    "raised_denial_error",
    "seat_anonymous",
    "session_gate_failure",
]
