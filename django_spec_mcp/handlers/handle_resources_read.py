from __future__ import annotations

from typing import Any, cast

from django_service_specs import resolve_callable_kwargs, run_selector, seeded_pool

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import (
    KernelAdmission,
    admit_kernel_read,
    call_context,
    carry_token,
)
from django_spec_mcp.handlers.read_kernel_resource import read_kernel_resource
from django_spec_mcp.handlers.runs_on_kernel import runs_on_kernel
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.utils import (
    consume_rate_limits,
    forbidden,
    rate_limited,
    resolve_resource_read,
    resource_read_answer,
    span_attrs,
)
from django_spec_mcp.output.build_resource_contents import build_resource_contents
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding


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
    target = resolve_resource_read(params, context)
    if isinstance(target, JsonRpcError):
        return target
    binding, vars_, uri = target

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
        # The seeds resolve under the call's context, read under the resource's
        # name, as a kernel resource read's do. So a seed registered as
        # ``request`` is the value rather than a collision with the transport's
        # own request
        # (``test_a_registered_request_seed_is_the_value_a_view_receives``), and
        # a seed whose resolver declares ``request``, ``view`` or
        # ``call_context`` is asked for it rather than raising ``TypeError``
        # (``test_a_seed_resolver_declaring_view_is_resolved``,
        # ``test_a_seed_resolver_declaring_call_context_reads_the_resource``). The
        # transport's request is added to the seeded pool, and only then do the
        # URI-template variables go under it, so none can be shadowed from the
        # URI, a variable named ``request`` included
        # (``test_a_uri_variable_named_request_never_shadows_the_transports_entry``).
        request = carry_token(context.http_request, context.token)
        seeded = seeded_pool(
            user=context.token.user,
            seeds=context.pool_seeds,
            call_context=call_context(request, binding.name),
        )
        seeded.setdefault("request", request)
        pool: dict[str, Any] = {**vars_, **seeded}
        raw: Any = run_selector(binding.selector, resolve_callable_kwargs(binding.selector, pool))
        contents: ResourceContents | JsonRpcError = build_resource_contents(
            binding=binding, uri=uri, raw=raw
        )
        if isinstance(contents, JsonRpcError):
            return contents
        return resource_read_answer(contents, binding, uri, context)


__all__ = ["handle_resources_read"]
