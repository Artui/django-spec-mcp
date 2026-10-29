from __future__ import annotations

from typing import Any

from django_service_specs import resolve_callable_kwargs

from django_spec_mcp._compat.acall import acall
from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import carry_token
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    consume_rate_limits,
    forbidden,
    prompt_get_answer,
    rate_limited,
    resolve_prompt_get,
    span_attrs,
)
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError


async def handle_prompts_get_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``handle_prompts_get``.

    The render callable is dispatched via ``acall``, so an async render
    function awaits directly and a sync one runs in a thread.
    """
    target = resolve_prompt_get(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, arguments_raw = target

    with span("mcp.prompts.get", attributes=span_attrs(binding.name, context)):
        # See the sync sibling. Off the loop, since a permission may query.
        allowed, required_scopes = await acall(judge_binding, binding, context.token)
        if not allowed:
            return forbidden(required_scopes)

        retry_after: int | None = await acall(
            consume_rate_limits, binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after)

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

        return prompt_get_answer(binding, raw, context)


__all__ = ["handle_prompts_get_async"]
