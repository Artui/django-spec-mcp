from __future__ import annotations

from typing import Any

from django_service_specs import resolve_callable_kwargs

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


def handle_prompts_get(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Render a registered prompt for the supplied arguments.

    Checks that required arguments are present, runs the binding's permission stack,
    then dispatches the render callable via ``resolve_callable_kwargs`` so it can
    declare any subset of ``request`` / ``user`` plus its per-prompt arguments. Whatever
    it returns is normalised into a list of
    [`PromptMessage`][django_spec_mcp.protocol.types.prompt_message.PromptMessage].

    The ``request`` / ``user`` seeds outrank the client's arguments, so a prompt
    argument named after one of them cannot stand in for the authenticated
    identity, and the finished result is held to ``MAX_RESULT_BYTES`` like every
    other result surface.
    """
    target = resolve_prompt_get(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, arguments_raw = target

    with span("mcp.prompts.get", attributes=span_attrs(binding.name, context)):
        # The prompt's scope gate and its kernel checks, asked of the principal
        # and this binding, as its listing asks them.
        allowed, required_scopes = judge_binding(binding, context.token)
        if not allowed:
            return forbidden(required_scopes)

        retry_after: int | None = consume_rate_limits(
            binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after)

        # The request the kernel tool path hands a spec: the transport's own
        # ``HttpRequest``, the resolved token on it as ``mcp_token``
        # (``test_a_render_declaring_request_receives_the_transports_http_request``).
        # Its ``user`` is the token's principal, and the ``user`` seed is that
        # same principal, so a render declaring ``user`` and one reading
        # ``request.user`` agree, an anonymous caller included
        # (``test_user_and_request_user_agree_for_a_token_naming_no_one``). Its
        # query string is the endpoint's: a render reads the prompt's arguments
        # by name, never off the request.
        request = carry_token(context.http_request, context.token)
        # The transport's seeds are applied *after* the client-supplied
        # arguments, so an argument named ``user`` or ``request`` shadows
        # nothing — the precedence ``RESERVED_POOL_SEEDS`` enforces on the
        # dispatch path, and the ordering ``completion/complete`` already uses.
        # A render callable declaring ``user`` therefore always receives the
        # authenticated principal, never a value the caller chose.
        pool: dict[str, Any] = {
            **arguments_raw,
            "request": request,
            "user": request.user,
        }
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.render, pool)
        raw: Any = binding.render(**kwargs)

        return prompt_get_answer(binding, raw, context)


__all__ = ["handle_prompts_get"]
