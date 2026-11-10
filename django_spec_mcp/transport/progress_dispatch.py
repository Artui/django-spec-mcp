from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import StreamingHttpResponse
from django_service_specs import ProgressReporter, apply_server_arguments

from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    admit_kernel_tool_call,
    tool_call_context,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import forbidden, judge_tool_permissions
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.response_stream import build_response_stream
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.progress_token_meta_key import PROGRESS_TOKEN_META_KEY


def progress_token(params: Any) -> str | int | None:
    """The token by which a client asked to hear about this request's progress."""
    if not isinstance(params, dict):
        return None
    meta: Any = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    token: Any = meta.get(PROGRESS_TOKEN_META_KEY)
    if isinstance(token, bool) or not isinstance(token, str | int):
        return None
    return token


def can_report_progress(method: str, params: Any, context: MCPCallContext) -> bool:
    """Whether this request's dispatch can actually emit progress."""
    if method != "tools/call" or not isinstance(params, dict):
        return False
    binding = _tool_binding(params, context)
    return binding is not None


def preflight_admission(
    method: str, params: Any, context: MCPCallContext
) -> JsonRpcError | KernelAdmission | None:
    """Run a tool's permission stack *before* deciding to stream."""
    if method != "tools/call" or not isinstance(params, dict):
        return None
    binding = _tool_binding(params, context)
    if binding is None:
        return None
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
    """The kernel spec's own checks on this call: its admission, or the denial."""
    dispatched = tool_call_context(binding, context)
    laid: dict[str, Any] = apply_server_arguments(
        binding.spec, arguments, binding.server_arguments, call_context=dispatched
    )
    return admit_kernel_tool_call(binding, laid, context, dispatched)


def _tool_binding(params: dict[str, Any], context: MCPCallContext) -> Any:
    """The binding a ``tools/call`` names, or ``None`` if it names none."""
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
    "preflight_admission",
    "progress_token",
    "stream_with_progress",
]
