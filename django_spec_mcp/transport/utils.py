from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View

from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.auth.unauthenticated_response import build_unauthenticated_response
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.kernel_utils import carry_token, seat
from django_spec_mcp.observability.session_fingerprint import session_fingerprint
from django_spec_mcp.protocol.parse_message import parse_message
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.json_rpc_message import JsonRpcMessage
from django_spec_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from django_spec_mcp.transport.origin_validation import is_origin_allowed
from django_spec_mcp.transport.types.request_metadata import RequestMetadata
from django_spec_mcp.transport.validate_modern_request import (
    VERSION_HEADER,
    validate_modern_request,
)
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.mcp_error_header import MCP_ERROR_HEADER
from django_spec_mcp.types.modern_protocol_versions import MODERN_PROTOCOL_VERSIONS
from django_spec_mcp.types.session_missing_hint import SESSION_MISSING_HINT
from django_spec_mcp.types.session_unknown_hint import SESSION_UNKNOWN_HINT

NOT_ACCEPTABLE_DETAIL: str = "Could not satisfy the request Accept header."
"""The ``406`` body's ``detail``, worded as the endpoints have always answered it."""


def not_acceptable(request: HttpRequest, media_types: tuple[str, ...]) -> JsonResponse | None:
    """The ``406`` for an ``Accept`` header admitting none of ``media_types``, else ``None``."""
    if any(request.accepts(media_type) for media_type in media_types):
        return None
    return JsonResponse({"detail": NOT_ACCEPTABLE_DETAIL}, status=406)


def allowed_methods(view: View) -> list[str]:
    """The methods ``view`` answers, upper-cased, for an ``Allow`` header."""
    return [method.upper() for method in view.http_method_names if hasattr(view, method)]


def exempt_endpoint(view: Callable[..., Any]) -> Any:
    """Mark a view function as opting out of CSRF and of ``LoginRequiredMiddleware``."""
    view.csrf_exempt = True  # ty: ignore[unresolved-attribute]
    view.login_required = False  # ty: ignore[unresolved-attribute]
    return view


def carry_principal(request: HttpRequest, token: TokenInfo) -> None:
    """Put the token on ``request`` as ``mcp_token``, and its principal as the user."""
    carry_token(request, token)


def seat_anonymous(request: HttpRequest) -> None:
    """Clear whatever user the middleware resolved, before the auth backend runs."""
    seat(request, AnonymousUser())


def is_raised_denial(exc: BaseException) -> bool:
    """Whether an exception escaping dispatch is a permission denial, not a fault."""
    return isinstance(exc, PermissionDenied)


def _is_permission_denial(result: Any) -> bool:
    """Whether a dispatch result is a permission denial needing HTTP 403."""
    return isinstance(result, JsonRpcError) and result.code == JsonRpcErrorCode.FORBIDDEN


def raised_denial_error() -> JsonRpcError:
    """The ``FORBIDDEN`` a handler returns for a denial, for one that was raised."""
    return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")


def _insufficient_scope_challenge(result: JsonRpcError, backend: MCPAuthBackend) -> str:
    """Build the ``WWW-Authenticate`` challenge for a permission denial."""
    data: Any = result.data if isinstance(result.data, dict) else {}
    scopes: list[str] | None = data.get("requiredScopes") or None
    return backend.www_authenticate_challenge(
        scopes=scopes, error="insufficient_scope" if scopes else None
    )


def _modern_error_status(error: JsonRpcError) -> int:
    """HTTP status for a JSON-RPC error on the modern transport."""
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


def _session_gate_failure(
    session_id: str | None, *, owner_matches: bool
) -> tuple[str, int, str] | None:
    """Decide the legacy session gate's outcome."""
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
        response[MCP_ERROR_HEADER] = error_hint
    return response


def internal_error_response(message: JsonRpcRequest, logger: logging.Logger) -> JsonResponse:
    """Answer a dispatch that raised with a JSON-RPC ``-32603``, and log why."""
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
    """A message's ``params`` when it is an object, else ``None``."""
    if isinstance(params, dict):
        return params
    return None


def session_rejection(
    session_id: str | None,
    *,
    owner_matches: bool,
    principal: str,
    message: JsonRpcMessage,
    logger: logging.Logger,
) -> JsonResponse | None:
    """The legacy session gate's refusal, logged, or ``None`` when the request passes."""
    failure = _session_gate_failure(session_id, owner_matches=owner_matches)
    if failure is None:
        return None
    message_text, status, hint = failure
    logger.warning(
        "Session rejected: %s (session=%s, principal=%s, method=%s) -> HTTP %s",
        hint,
        session_fingerprint(session_id),
        principal,
        getattr(message, "method", "?"),
        status,
    )
    return error_response(
        code=JsonRpcErrorCode.INVALID_REQUEST,
        message=message_text,
        status=status,
        request_id=getattr(message, "id", None),
        error_hint=hint,
    )


def read_message(http_request: HttpRequest, config: MCPConfig) -> JsonRpcMessage | JsonResponse:
    """The one JSON-RPC message a POST carries, or the refusal its body earns."""
    if len(http_request.body) > config.max_request_bytes:
        return error_response(
            code=JsonRpcErrorCode.INVALID_REQUEST,
            message="Request body too large",
            status=413,
        )

    try:
        payload: Any = json.loads(http_request.body or b"null")
    except (ValueError, RecursionError) as exc:
        detail: str = (
            exc.msg if isinstance(exc, json.JSONDecodeError) else "body could not be decoded"
        )
        return error_response(code=JsonRpcErrorCode.PARSE_ERROR, message=f"Invalid JSON: {detail}")

    try:
        return parse_message(payload)
    except ValueError as exc:
        return error_response(code=JsonRpcErrorCode.INVALID_REQUEST, message=str(exc))


def origin_refusal(http_request: HttpRequest, config: MCPConfig) -> JsonResponse | None:
    """The ``403`` for an ``Origin`` outside ``allowed_origins``, else ``None``."""
    origin: str | None = http_request.headers.get("Origin")
    if is_origin_allowed(origin, config.allowed_origins):
        return None
    return error_response(
        code=JsonRpcErrorCode.INVALID_REQUEST,
        message=f"Origin not allowed: {origin!r}",
        status=403,
    )


def unauthenticated_response(backend: MCPAuthBackend, request: HttpRequest) -> HttpResponse:
    """The ``401`` for a caller the backend could not authenticate, with its challenge."""
    error: str | None = "invalid_token" if "Authorization" in request.headers else None
    return build_unauthenticated_response(backend.www_authenticate_challenge(error=error))


def unsupported_version_response(request_id: Any = None) -> JsonResponse:
    """The ``400`` for a legacy request whose version header did not negotiate."""
    return error_response(
        code=JsonRpcErrorCode.INVALID_REQUEST,
        message="Missing or unsupported MCP-Protocol-Version",
        request_id=request_id,
    )


def unknown_session_response() -> JsonResponse:
    """The ``404`` for a GET or DELETE naming no session the caller owns."""
    return error_response(
        code=JsonRpcErrorCode.INVALID_REQUEST,
        message="Unknown or missing MCP-Session-Id",
        status=404,
    )


def modern_era_requested(http_request: HttpRequest) -> bool:
    """Whether the caller named a modern revision in its version header."""
    return http_request.headers.get(VERSION_HEADER) in MODERN_PROTOCOL_VERSIONS


def modern_refusal(error: JsonRpcError, request_id: Any) -> JsonResponse:
    """A modern-era refusal raised before dispatch, under its normative status."""
    return error_response(
        code=error.code,
        message=error.message,
        data=error.data,
        status=_modern_error_status(error),
        request_id=request_id,
    )


def modern_header_refusal(
    http_request: HttpRequest,
    message: JsonRpcMessage,
    metadata: RequestMetadata,
    config: MCPConfig,
) -> JsonResponse | None:
    """The refusal for a modern request whose headers disagree with its body, else ``None``."""
    if not isinstance(message, JsonRpcRequest):
        return None
    invalid: JsonRpcError | None = validate_modern_request(
        method=message.method,
        params=params_dict(message.params),
        metadata=metadata,
        headers=http_request.headers,
        supported_versions=config.modern_protocol_versions,
    )
    if invalid is None:
        return None
    return modern_refusal(invalid, message.id)


def challenged_error_response(
    error: JsonRpcError, *, request_id: Any, status: int, backend: MCPAuthBackend
) -> JsonResponse:
    """``error`` under ``status``; a ``403`` also carries the scope challenge."""
    response = JsonResponse(JsonRpcResponse(id=request_id, error=error).to_dict(), status=status)
    if status == 403:
        response["WWW-Authenticate"] = _insufficient_scope_challenge(error, backend)
    return response


def legacy_result_response(request_id: Any, result: Any, backend: MCPAuthBackend) -> JsonResponse:
    """A legacy-era dispatch result as its response."""
    if not isinstance(result, JsonRpcError):
        return JsonResponse(JsonRpcResponse(id=request_id, result=result).to_dict())
    status: int = 403 if _is_permission_denial(result) else 200
    return challenged_error_response(result, request_id=request_id, status=status, backend=backend)


def modern_result_response(request_id: Any, result: Any, backend: MCPAuthBackend) -> JsonResponse:
    """A modern-era dispatch result as its response, an error under ``_modern_error_status``."""
    if not isinstance(result, JsonRpcError):
        return JsonResponse(JsonRpcResponse(id=request_id, result=result).to_dict())
    return challenged_error_response(
        result, request_id=request_id, status=_modern_error_status(result), backend=backend
    )


__all__ = [
    "NOT_ACCEPTABLE_DETAIL",
    "allowed_methods",
    "carry_principal",
    "challenged_error_response",
    "error_response",
    "exempt_endpoint",
    "internal_error_response",
    "is_raised_denial",
    "legacy_result_response",
    "modern_era_requested",
    "modern_header_refusal",
    "modern_refusal",
    "modern_result_response",
    "not_acceptable",
    "origin_refusal",
    "params_dict",
    "raised_denial_error",
    "read_message",
    "seat_anonymous",
    "session_rejection",
    "unauthenticated_response",
    "unknown_session_response",
    "unsupported_version_response",
]
