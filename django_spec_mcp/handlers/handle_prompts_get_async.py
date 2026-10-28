from __future__ import annotations

from typing import Any

from django_service_specs import resolve_callable_kwargs

from django_spec_mcp._compat.acall import acall
from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.handle_tools_call import _span_attrs
from django_spec_mcp.handlers.kernel_utils import carry_token
from django_spec_mcp.handlers.render_prompt_messages import normalize_render_result
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    forbidden,
)
from django_spec_mcp.output.enforce_result_bytes import enforce_result_bytes
from django_spec_mcp.protocol.types.get_prompt_result import GetPromptResult
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


async def handle_prompts_get_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``handle_prompts_get``.

    The render callable is dispatched via ``acall``, so an async render
    function awaits directly and a sync one runs in a thread.
    """
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "prompts/get params must be an object")
    name: Any = params.get("name")
    if not isinstance(name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.prompts.get(name)
    if binding is None:
        # See the sync sibling: the prompts spec names ``-32602`` for an
        # invalid prompt name.
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown prompt: {name!r}")

    arguments_raw: Any = params.get("arguments")
    if arguments_raw is None:
        arguments_raw = {}
    # Not ``or {}``: that folds every falsy value into the default, so a ``[]``,
    # ``""``, ``0`` or ``False`` would be accepted as "no arguments" by the very
    # line below that exists to reject a non-object.
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

    with span("mcp.prompts.get", attributes=_span_attrs(binding.name, context)):
        # See the sync sibling. Off the loop, since a permission may query.
        allowed, required_scopes = await acall(
            check_permissions,
            binding.permissions,
            context.http_request,
            context.token,
        )
        if not allowed:
            return forbidden(required_scopes)

        retry_after: int | None = await acall(
            consume_rate_limits, binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return JsonRpcError(
                JsonRpcErrorCode.RATE_LIMITED,
                "Rate limit exceeded",
                data={"retryAfter": retry_after},
            )

        # See the sync sibling: the transport's own ``HttpRequest``, carrying
        # the token, and the seeds land *after* the client-supplied arguments,
        # so an argument named ``user`` or ``request`` shadows nothing.
        request = carry_token(context.http_request, context.token)
        pool: dict[str, Any] = {
            **arguments_raw,
            "request": request,
            "user": request.user,
        }
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.render, pool)
        raw: Any = await acall(binding.render, **kwargs)

        try:
            messages = normalize_render_result(raw)
        except TypeError as exc:
            return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc))

        result: dict[str, Any] = GetPromptResult(
            messages=messages, description=binding.description
        ).to_dict()
        # See the sync sibling: a rendered prompt is bounded by
        # ``MAX_RESULT_BYTES`` like every other result surface.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Prompt {binding.name!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_prompts_get_async"]
