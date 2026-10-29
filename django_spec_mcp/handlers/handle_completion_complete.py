from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Any

from django_service_specs import RESERVED_POOL_SEEDS, NotPermitted, resolve_callable_kwargs

from django_spec_mcp._compat.tracing import span
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.kernel_utils import (
    admits_without_arguments,
    call_context,
    carry_token,
    principal_of,
    resource_refusal,
)
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.handlers.utils import (
    consume_rate_limits,
    forbidden,
    rate_limited,
    span_attrs,
)
from django_spec_mcp.protocol.types.completion import Completion
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.max_completion_values import MAX_COMPLETION_VALUES


def handle_completion_complete(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Suggest values for one argument of a prompt or resource template.

    The interactive-typing surface: a client shows a dropdown as the user fills
    in a prompt argument or a URI-template variable and asks here for
    candidates. Completers are registered per binding, keyed by the argument
    they complete, and dispatched through ``resolve_callable_kwargs``, so
    one declares whichever of ``value`` / ``arguments`` / ``request`` / ``user``
    it needs (plus any already-resolved sibling by name) and gets nothing else.
    A sibling named after a dispatcher-owned seed is dropped from that spread,
    so no client-chosen value ever binds to one of those parameter names.

    **A completion is a read of the thing being completed**, so the binding's
    permission and rate-limit stacks run before the completer. Without that, a
    resource a caller may not read would still answer "which ids exist?" one
    keystroke at a time.

    **Completers are sync callables.** Under ASGI this handler runs in Django's
    thread-sensitive executor (see ``adispatch``), so ORM access inside a
    completer is fine — but returning a coroutine is not, since nothing awaits
    it.

    Errors are all ``-32602`` per the completion spec: an unknown prompt or
    template, an argument with no completer, or a malformed ``ref``.
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "completion/complete params must be an object"
        )

    argument: Any = params.get("argument")
    if not isinstance(argument, dict) or not isinstance(argument.get("name"), str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "'argument' is required and must be an object with a string 'name'",
        )
    argument_name: str = argument["name"]
    # The spec types ``value`` as the text typed so far, which is legitimately
    # empty when the field has just been focused.
    value: Any = argument.get("value", "")
    if not isinstance(value, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'argument.value' must be a string")

    target = _resolve_ref(params.get("ref"), context)
    if isinstance(target, JsonRpcError):
        return target

    completer: Callable[..., Any] | None = target.completions.get(argument_name)
    if completer is None:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            f"{target.label} has no completer for argument {argument_name!r}",
            data={"completable": sorted(target.completions)},
        )

    context_raw: Any = params.get("context") or {}
    siblings: Any = context_raw.get("arguments") if isinstance(context_raw, dict) else None
    resolved_siblings: dict[str, Any] = siblings if isinstance(siblings, dict) else {}

    attributes: dict[str, Any] = {
        **span_attrs(target.name, context),
        "mcp.completion.kind": target.kind,
        "mcp.completion.argument": argument_name,
    }
    with span("mcp.completion.complete", attributes=attributes):
        allowed, required_scopes = judge_binding(target.binding, context.token)
        if not allowed:
            return forbidden(required_scopes)

        # Before the charge, where the read asks the spec's checks too
        # (``test_a_kernel_resources_spec_is_asked_before_its_rate_limit``).
        refused: JsonRpcError | None = _refused_by_the_spec(target, context)
        if refused is not None:
            return refused

        retry_after: int | None = consume_rate_limits(
            target.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return rate_limited(retry_after)

        # The request the kernel tool path hands a spec: the transport's own
        # ``HttpRequest``, the resolved token on it as ``mcp_token``
        # (``test_a_completer_declaring_request_receives_the_transports_http_request``).
        # Its ``user`` is the token's principal, and the ``user`` seed is that
        # same principal, an anonymous caller included
        # (``test_a_completers_user_and_request_user_agree_for_a_token_naming_no_one``).
        # Its query string is the endpoint's: a completer reads what the client
        # typed from ``value`` and ``arguments``, never from the request.
        request = carry_token(context.http_request, context.token)
        # Every dispatcher-owned name is dropped from the client's siblings
        # before the spread, then the transport's own seeds are applied on top.
        # Re-seeding alone would cover only the names this handler supplies, so
        # a sibling called ``instance`` or ``collection`` (reserved everywhere
        # else, seeded nowhere here) would reach a completer under a name the
        # rest of the package treats as dispatcher-owned; stripping tracks
        # ``RESERVED_POOL_SEEDS`` rather than a hand-picked subset of it.
        # ``request`` is no kernel seed, and needs no strip: the seed below is
        # written over a sibling of that name.
        # ``arguments`` stays the client's mapping verbatim: it is documented as
        # the siblings the client resolved, and a completer reading it knows it
        # is reading caller input.
        pool: dict[str, Any] = {
            **{key: v for key, v in resolved_siblings.items() if key not in RESERVED_POOL_SEEDS},
            "request": request,
            "user": request.user,
            "value": value,
            "arguments": resolved_siblings,
        }
        raw: Any = completer(**resolve_callable_kwargs(completer, pool))
        return {"completion": _to_completion(raw).to_dict()}


@dataclass(frozen=True)
class _CompletionTarget:
    """The slice of a prompt or resource binding a completion request needs.

    The two binding types have no common base, so flattening them here is what
    keeps the rest of the handler from branching on which kind it got.
    """

    kind: str
    """``prompt`` or ``resource`` — used to label errors and the span."""

    name: str
    binding: PromptBinding | ResourceBinding
    """The prompt or resource itself, which its checks are handed as the spec."""
    rate_limits: tuple[Any, ...]
    completions: dict[str, Callable[..., Any]]
    kernel: KernelResourceBinding | None = None
    """The kernel resource completed, whose spec's own checks answer too; else ``None``."""

    @property
    def label(self) -> str:
        return f"{self.kind.capitalize()} {self.name!r}"


def _resolve_ref(ref: Any, context: MCPCallContext) -> _CompletionTarget | JsonRpcError:
    """Look up the prompt or resource a ``ref`` names."""
    if not isinstance(ref, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'ref' is required and must be an object"
        )
    ref_type: Any = ref.get("type")

    if ref_type == "ref/prompt":
        name: Any = ref.get("name")
        if not isinstance(name, str):
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS, "ref/prompt requires a string 'name'"
            )
        prompt = context.prompts.get(name)
        if prompt is None:
            return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown prompt: {name!r}")
        return _CompletionTarget(
            kind="prompt",
            name=name,
            binding=prompt,
            rate_limits=prompt.rate_limits,
            completions=prompt.completions,
        )

    if ref_type == "ref/resource":
        uri: Any = ref.get("uri")
        if not isinstance(uri, str):
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS, "ref/resource requires a string 'uri'"
            )
        # The spec's example passes the *template* (``file:///{path}``), which
        # is what a client holds while the user is still typing. Matched
        # exactly first, because a template also satisfies its own pattern and
        # would otherwise "resolve" with ``path="{path}"``.
        resource = context.resources.by_uri_template(uri)
        if resource is None:
            return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown resource: {uri!r}")
        return _CompletionTarget(
            kind="resource",
            name=resource.name,
            binding=resource,
            rate_limits=resource.rate_limits,
            completions=resource.completions,
            kernel=resource if isinstance(resource, KernelResourceBinding) else None,
        )

    return JsonRpcError(
        JsonRpcErrorCode.INVALID_PARAMS,
        f"Unsupported completion ref type: {ref_type!r}. Expected 'ref/prompt' or 'ref/resource'.",
    )


def _refused_by_the_spec(target: _CompletionTarget, context: MCPCallContext) -> JsonRpcError | None:
    """The refusal a kernel resource's spec answers its completion with, or ``None``.

    A kernel resource binding's ``permissions`` hold only its scope gate, since
    its spec's checks are the kernel's and ``dispatch`` asks them at the read.
    A completion dispatches nothing, so without this the scope gate alone
    answered it, and a spec refusing the caller still listed which values exist
    (``test_a_kernel_resources_completion_asks_its_spec_permissions``). Asked
    before the rate limit, where the read asks them (``admit_kernel_read``).

    A completion names a template rather than a URI, so it has no arguments,
    and is judged as the listing judges the spec, through the same
    ``admits_without_arguments``: a check scoping by a variable decides on
    nothing and refuses, whether it guards against ``None`` and answers no
    (``test_a_kernel_resources_completion_is_judged_on_no_arguments``) or
    indexes it and raises
    (``test_a_check_raising_on_no_arguments_refuses_the_completion_as_the_read_refuses``),
    and a check raising a ``ServiceError`` refuses it as it hides the template
    (``test_a_check_raising_a_service_error_refuses_the_completion_as_the_listing``).
    A configuration error is raised, as the listing and the read raise it
    (``test_a_misconfigured_check_fails_the_completion_as_the_read``).
    """
    binding = target.kernel
    if binding is None:
        return None
    if admits_without_arguments(
        binding,
        principal_of(context.token.user),
        config=context.config,
        pool_seeds=context.pool_seeds,
        call_context=call_context(carry_token(context.http_request, context.token), binding.name),
    ):
        return None
    return resource_refusal(
        NotPermitted(),
        KernelRefusalScene(arguments={}, config=context.config, conventions=context.conventions),
    )


def _to_completion(raw: Any) -> Completion:
    """Cap a completer's output at the spec's limit without draining it.

    One value past the cap is pulled to decide ``hasMore`` and discarded, so a
    completer returning a queryset or generator is sliced rather than
    materialised — 101 rows read instead of the table.
    """
    values: list[str] = [str(item) for item in islice(_iterable(raw), MAX_COMPLETION_VALUES + 1)]
    has_more: bool = len(values) > MAX_COMPLETION_VALUES
    return Completion(values=tuple(values[:MAX_COMPLETION_VALUES]), has_more=has_more)


def _iterable(raw: Any) -> Iterable[Any]:
    """A lone string is one suggestion, not a sequence of characters."""
    return [raw] if isinstance(raw, str) else raw


__all__ = ["handle_completion_complete"]
