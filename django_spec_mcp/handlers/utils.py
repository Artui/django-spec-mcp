from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest
from django_service_specs import (
    ActionUnavailable,
    ServiceError,
    ServiceValidationError,
    UnsetType,
    apply_server_arguments,
)

from django_spec_mcp._compat.reject_awaitable import reject_awaitable
from django_spec_mcp.auth.rate_limits.types.mcp_rate_limit import MCPRateLimit
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.pagination import paginate
from django_spec_mcp.handlers.render_prompt_messages import normalize_render_result
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.observability.get_logger import get_logger
from django_spec_mcp.output.enforce_result_bytes import enforce_result_bytes
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.get_prompt_result import GetPromptResult
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.schema.utils import end_sentence
from django_spec_mcp.types.cache_scope import CacheScope
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.modern_protocol_versions import MODERN_PROTOCOL_VERSIONS

logger = get_logger(__name__)


def apply_tool_server_arguments(
    binding: Any, arguments: dict[str, Any], *, dispatch_context: dict[str, Any]
) -> dict[str, Any]:
    """``arguments`` with the binding's ``server_arguments`` in place, the caller's dropped."""
    server = binding.server_arguments
    if not server.names():
        return arguments
    applied = apply_server_arguments(binding.spec, arguments, server, call_context=dispatch_context)
    dropped = sorted(
        name
        for name in server.names() & arguments.keys()
        if name in server.withheld or repr(arguments[name]) != repr(applied[name])
    )
    if dropped:
        logger.warning(
            "MCP tool %r: dropped the caller's value for %s, which the tool's server_arguments "
            "supply or withhold.",
            binding.name,
            ", ".join(dropped),
        )
    return applied


def scope_verdict(scopes: tuple[str, ...] | None, token: TokenInfo) -> tuple[bool, list[str]]:
    """A binding's scope gate judged against ``token``: ``(allowed, required_scopes)``."""
    declared = scopes or ()
    if token.has_all_scopes(declared):
        return True, []
    return False, list(declared)


def judge_tool_permissions(
    binding: KernelToolBinding, context: MCPCallContext
) -> tuple[bool, list[str]]:
    """A tool binding's scope gate, judged for this call."""
    return scope_verdict(binding.scopes, context.token)


def forbidden(required_scopes: list[str]) -> JsonRpcError:
    """The ``-32006`` for a caller a binding's ``permissions`` deny."""
    return JsonRpcError(
        JsonRpcErrorCode.FORBIDDEN,
        "Insufficient permission",
        data={"requiredScopes": required_scopes} if required_scopes else None,
    )


def consume_rate_limits(
    rate_limits: tuple[Any, ...],
    http_request: HttpRequest,
    token: TokenInfo,
) -> int | None:
    """Run every rate limiter in order, returning the largest retry-after."""
    for limiter in rate_limits:
        if not isinstance(limiter, MCPRateLimit):
            continue  # pragma: no cover
        retry_after: int | None = reject_awaitable(
            limiter.consume(http_request, token),
            call=f"{type(limiter).__name__}.consume()",
            remedy=(
                "MCP rate limiters are synchronous by contract: consume must be a plain "
                "'def'. Wrap any awaiting it needs in asgiref.sync.async_to_sync inside "
                "the method body."
            ),
            hazard=(
                "an un-awaited coroutine is not None, so every call would be denied with "
                "the coroutine object as its retryAfter."
            ),
        )
        if retry_after is not None:
            return retry_after
    return None


def rate_limited(retry_after: int) -> JsonRpcError:
    """The error every method answers when ``consume_rate_limits`` denied it."""
    return JsonRpcError(
        JsonRpcErrorCode.RATE_LIMITED,
        "Rate limit exceeded",
        data={"retryAfter": retry_after},
    )


def effective_rate_limits(binding: Any, context: MCPCallContext) -> tuple[Any, ...]:
    """A tool binding's rate limiters, or none when this dispatch must not charge."""
    return binding.rate_limits if context.enforce_rate_limits else ()


def validation_error_data(detail: Any, value: Any, *, include_value: bool) -> dict[str, Any]:
    """Build the ``detail`` / ``value`` pair a validation failure reports."""
    payload: dict[str, Any] = {"detail": detail}
    if include_value:
        payload["value"] = value
    return payload


def render_convention(template: str | None) -> str | None:
    """One placeholder-free ``AgentConventions`` field as the model reads it."""
    return None if template is None else template.format()


def missing_arguments_message(names: Iterable[str], conventions: AgentConventions) -> str:
    """The server's ``missing_arguments`` sentence for a call that left ``names`` out."""
    listed = ", ".join(f"`{name}`" for name in names)
    return conventions.missing_arguments.format(names=listed)


def validation_error_result(
    exc: ServiceValidationError,
    arguments: Any,
    *,
    config: MCPConfig,
) -> ToolResult:
    """The ``isError`` result for a tool call whose arguments a service refused."""
    return build_error_tool_result(
        exc.message,
        error_type="validation_error",
        detail=validation_error_data(
            exc.detail, arguments, include_value=config.include_validation_value
        ),
    )


def service_error_result(exc: ServiceError) -> ToolResult:
    """The ``isError`` tool result for a ``ServiceError`` a dispatch raised."""
    error_detail: dict[str, Any] = {}
    if isinstance(exc, ActionUnavailable):
        error_detail["code"] = exc.code
    return build_error_tool_result(exc.message, error_type="service_error", detail=error_detail)


def rendering_refusal_result(
    exc: ServiceValidationError,
    *,
    supplied: list[str],
    arguments: Mapping[str, Any],
    paginated: bool,
    config: MCPConfig,
    conventions: AgentConventions,
) -> ToolResult:
    """The ``validation_error`` result for a refusal of ``supplied`` while rendering."""
    if not supplied:
        raise exc
    detail: Any = exc.detail
    key: str = supplied[0] if len(supplied) == 1 else "non_field_errors"
    message: str = (
        f"{_name_list(supplied)} was rejected while rendering the result: "
        f"{_readable_detail(detail)}"
    )
    scope = render_convention(conventions.query_param_on_pages)
    if paginated and scope is not None:
        message = f"{message} {scope}"
    return build_error_tool_result(
        message,
        error_type="validation_error",
        detail=validation_error_data(
            {key: detail if isinstance(detail, (list, dict)) else [detail]},
            arguments,
            include_value=config.include_validation_value,
        ),
    )


def _name_list(names: list[str]) -> str:
    """``names`` as prose: "`a`", or "`a`, `b` or `c`" when there are several."""
    quoted: list[str] = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _readable_detail(detail: Any) -> str:
    """A validation ``detail`` as sentences a model reads, not as a ``repr``."""
    if isinstance(detail, dict):
        return " ".join(f"`{key}`: {_readable_detail(value)}" for key, value in detail.items())
    if isinstance(detail, list):
        return " ".join(_readable_detail(item) for item in detail)
    return end_sentence(str(detail))


def resolve_tool_call(
    params: dict[str, Any], context: MCPCallContext
) -> tuple[KernelToolBinding, dict[str, Any]] | JsonRpcError:
    """The ``tools/call`` request both transports start from: the binding it names."""
    tool_name: Any = params.get("name")
    if not isinstance(tool_name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.tools.get(tool_name)
    if binding is None:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown tool: {tool_name!r}")

    arguments_raw: Any = params.get("arguments")
    if arguments_raw is None:
        arguments_raw = {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    return binding, arguments_raw


def resolve_prompt_get(
    params: dict[str, Any] | None, context: MCPCallContext
) -> tuple[PromptBinding, dict[str, Any]] | JsonRpcError:
    """The ``prompts/get`` request both transports start from: the binding it names."""
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "prompts/get params must be an object")
    name: Any = params.get("name")
    if not isinstance(name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.prompts.get(name)
    if binding is None:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown prompt: {name!r}")

    arguments_raw: Any = params.get("arguments")
    if arguments_raw is None:
        arguments_raw = {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    missing: list[str] = [
        arg.name for arg in binding.arguments if arg.required and arg.name not in arguments_raw
    ]
    if missing:
        data: dict[str, Any] = {"missing": missing}
        if context.config.include_validation_value:
            data["value"] = arguments_raw
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "Missing required prompt arguments",
            data=data,
        )
    return binding, arguments_raw


def prompt_get_answer(
    binding: PromptBinding, raw: Any, context: MCPCallContext
) -> dict[str, Any] | JsonRpcError:
    """The ``prompts/get`` answer for what a prompt's ``render`` returned."""
    try:
        messages = normalize_render_result(raw)
    except TypeError as exc:
        return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc))

    result: dict[str, Any] = GetPromptResult(
        messages=messages, description=binding.description
    ).to_dict()
    oversize: str | None = enforce_result_bytes(
        result, context.config.max_result_bytes, label=f"Prompt {binding.name!r}"
    )
    if oversize is not None:
        return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
    return result


def resolve_resource_read(
    params: dict[str, Any] | None, context: MCPCallContext
) -> tuple[ResourceBinding[Any], dict[str, str], str] | JsonRpcError:
    """The ``resources/read`` request both transports start from: the binding its URI names."""
    if not isinstance(params, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "resources/read params must be an object"
        )
    uri: Any = params.get("uri")
    if not isinstance(uri, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'uri' is required and must be a string"
        )

    try:
        resolved = context.resources.resolve(uri)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc), data={"uri": uri})
    if resolved is None:
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Unknown resource: {uri!r}",
            data={"uri": uri},
        )
    binding, vars_ = resolved
    return binding, vars_, uri


def resolve_bound(override: Any, default: Any) -> Any:
    """Resolve a per-binding outbound bound against the server's default."""
    return default if isinstance(override, UnsetType) else override


def resource_not_found_code(protocol_version: str) -> JsonRpcErrorCode:
    """Which code a missing ``resources/read`` target gets, by era."""
    if protocol_version in MODERN_PROTOCOL_VERSIONS:
        return JsonRpcErrorCode.INVALID_PARAMS
    return JsonRpcErrorCode.RESOURCE_NOT_FOUND


def catalog_cache_hints(*, ttl_ms: int, per_caller: bool) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a catalog result."""
    scope = CacheScope.PRIVATE if per_caller else CacheScope.PUBLIC
    return {"ttlMs": ttl_ms, "cacheScope": scope.value}


def catalog_cursor(params: dict[str, Any] | None) -> str | None | JsonRpcError:
    """The opaque ``cursor`` a list method was sent, ``None`` for the first page."""
    cursor: Any = (params or {}).get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'cursor' must be a string")
    return cursor


def catalog_page(
    key: str,
    bindings: Sequence[Any],
    cursor: str | None,
    entry: Callable[[Any], dict[str, Any]],
    context: MCPCallContext,
    *,
    per_caller: bool,
) -> dict[str, Any] | JsonRpcError:
    """One page of a list method's ``bindings``, each rendered by ``entry`` under ``key``."""
    try:
        page, next_cursor = paginate(bindings, cursor, page_size=context.config.page_size)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))
    response: dict[str, Any] = {
        key: [entry(binding) for binding in page],
        **catalog_cache_hints(ttl_ms=context.config.catalog_cache_ttl_ms, per_caller=per_caller),
    }
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


def resource_cache_hints(ttl_ms: int) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a ``resources/read`` result."""
    return {"ttlMs": ttl_ms, "cacheScope": CacheScope.PRIVATE.value}


def resource_read_answer(
    contents: ResourceContents, binding: ResourceBinding[Any], uri: str, context: MCPCallContext
) -> dict[str, Any] | JsonRpcError:
    """The ``resources/read`` answer around one encoded ``contents`` block."""
    answer: dict[str, Any] = {
        "contents": [contents.to_dict()],
        **resource_cache_hints(
            resolve_bound(binding.cache_ttl_ms, context.config.resource_cache_ttl_ms)
        ),
    }
    oversize: str | None = enforce_result_bytes(
        answer, context.config.max_result_bytes, label=f"Resource {uri!r}"
    )
    if oversize is not None:
        return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
    return answer


def enforce_result_ceiling(result: Any, *, max_result_bytes: int | None, label: str) -> Any:
    """Replace an over-ceiling tool result with an ``isError`` result."""
    if isinstance(result, JsonRpcError):
        return result
    message: str | None = enforce_result_bytes(result, max_result_bytes, label=label)
    if message is None:
        return result
    return build_error_tool_result(message, error_type="result_too_large").to_dict()


async def run_with_deadline(coro: Awaitable[Any], seconds: float | None) -> Any:
    """Await ``coro``, raising ``asyncio.TimeoutError`` past ``seconds``."""
    if seconds is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=seconds)


def span_attrs(binding_name: str, context: MCPCallContext) -> dict[str, Any]:
    """The span attributes every handler that opens a span sets, so they cannot drift apart."""
    attrs: dict[str, Any] = {
        "mcp.binding.name": binding_name,
        "mcp.protocol.version": context.protocol_version,
    }
    if context.session_id:
        attrs["mcp.session.id"] = context.session_id
    return attrs


__all__ = [
    "catalog_cursor",
    "catalog_page",
    "apply_tool_server_arguments",
    "consume_rate_limits",
    "effective_rate_limits",
    "enforce_result_ceiling",
    "judge_tool_permissions",
    "missing_arguments_message",
    "prompt_get_answer",
    "rate_limited",
    "render_convention",
    "rendering_refusal_result",
    "resolve_bound",
    "resolve_prompt_get",
    "resolve_resource_read",
    "resolve_tool_call",
    "resource_read_answer",
    "run_with_deadline",
    "scope_verdict",
    "span_attrs",
    "validation_error_data",
    "validation_error_result",
]
