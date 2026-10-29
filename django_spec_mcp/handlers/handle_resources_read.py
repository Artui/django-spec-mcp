from __future__ import annotations

from typing import Any, cast

from django_service_specs import base_pool, resolve_callable_kwargs, run_selector

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import KernelAdmission, admit_kernel_read, carry_token
from django_spec_mcp.handlers.read_kernel_resource import read_kernel_resource
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    consume_rate_limits,
    forbidden,
    rate_limited,
    resolve_bound,
    resource_cache_hints,
    resource_not_found_code,
    span_attrs,
)
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.output.enforce_result_bytes import enforce_result_bytes
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


def handle_resources_read(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Read a resource (or templated-resource instance) by URI.

    Resolves the URI through the registry, judges the binding's permissions
    and charges its rate limits, then reads it on the path its binding
    takes: a kernel resource through the kernel's ``dispatch``
    ([`read_kernel_resource`][django_spec_mcp.handlers.read_kernel_resource.read_kernel_resource]),
    and a view (an MCP Apps UI resource, or one registered over a plain
    callable) by running its selector over the kernel's pool. The answer is
    one ``ResourceContents`` block, encoded by ``build_resource_contents``.
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "resources/read params must be an object"
        )
    uri: Any = params.get("uri")
    if not isinstance(uri, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'uri' is required and must be a string"
        )

    resolved = context.resources.resolve(uri)
    if resolved is None:
        # ``-32002`` with the URI echoed in ``data`` is the spec's own worked
        # example, so a client special-casing resource not-found finds both
        # halves where it expects them.
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Unknown resource: {uri!r}",
            data={"uri": uri},
        )
    binding, vars_ = resolved

    with span(
        "mcp.resources.read",
        attributes={**span_attrs(binding.name, context), "mcp.resource.uri": uri},
    ):
        allowed, required_scopes = judge_binding(binding, context.token)
        if not allowed:
            return forbidden(required_scopes)

        # A kernel resource's binding holds only its scope gate,
        # and its spec's own checks run inside ``dispatch``, after the charge
        # below, so they are asked here first: a reader they refuse is not
        # charged
        # (``test_a_reader_the_spec_refuses_is_not_charged``). ``dispatch``
        # runs under the grant they gave, so an admitted read asks them once
        # (``test_an_admitted_reader_is_charged_once_and_read_once``).
        admission: KernelAdmission | None = None
        if runs_on_kernel(binding):
            admitted = admit_kernel_read(cast(KernelResourceBinding, binding), vars_, context)
            if isinstance(admitted, JsonRpcError):
                return admitted
            admission = admitted

        retry_after: int | None = consume_rate_limits(
            binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after)

        # A kernel resource reads through the kernel from here, the scope gate,
        # its spec's checks and the rate limits above having answered for it.
        if admission is not None:
            return read_kernel_resource(
                cast(KernelResourceBinding, binding), uri, vars_, admission, context
            )
        # A view reads through the kernel's pool and selector dispatch, and the
        # request the kernel tool path hands a spec: the transport's
        # ``HttpRequest`` with the token on it, which a seed declaring
        # ``request`` receives
        # (``test_a_pool_seed_resolved_on_a_view_read_is_handed_the_transports_request``).
        # The transport's seeds land after the URI-template variables, so none
        # can be shadowed from the URI.
        request = carry_token(context.http_request, context.token)
        pool: dict[str, Any] = {
            **vars_,
            **base_pool(user=context.token.user, request=request, seeds=context.pool_seeds),
        }
        raw: Any = run_selector(binding.selector, resolve_callable_kwargs(binding.selector, pool))
        contents: ResourceContents | JsonRpcError = build_resource_contents(
            binding=binding, uri=uri, raw=raw
        )
        if isinstance(contents, JsonRpcError):
            return contents
        result: dict[str, Any] = {
            "contents": [contents.to_dict()],
            **resource_cache_hints(
                resolve_bound(binding.cache_ttl_ms, context.config.resource_cache_ttl_ms)
            ),
        }
        # Same outbound ceiling as a tool result, different envelope: a resource
        # read has no ``isError`` shape to carry the explanation, so this is a
        # protocol error carrying the same remedy-naming message.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Resource {uri!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_resources_read"]
