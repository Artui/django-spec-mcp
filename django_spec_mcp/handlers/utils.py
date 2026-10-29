from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable, Mapping
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
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.observability.get_logger import get_logger
from django_spec_mcp.output.enforce_result_bytes import enforce_result_bytes
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.schema.utils import end_sentence
from django_spec_mcp.types.cache_scope import CacheScope
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.modern_protocol_versions import MODERN_PROTOCOL_VERSIONS

logger = get_logger(__name__)


def apply_tool_server_arguments(binding: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """``arguments`` with the binding's ``server_arguments`` in place, the caller's dropped.

    The first thing every ``tools/call`` route does with the arguments, once it
    knows they are an object: ``handle_tools_call`` and its async sibling
    before a task is created, the in-process ``MCPServer.call_tool`` before it
    runs the tool. So everything after reads the server's value - the kernel's
    admission and its required-argument check - and a bound value fills a
    required read rather than being refused as missing before it arrives. A
    task is recorded with the arguments this returns, so it runs with the
    bound value (``test_a_kernel_task_is_recorded_and_run_with_the_bound_value``).

    The rule is the kernel's ``apply_server_arguments``: the caller's value for
    every bound or withheld name is dropped before anything reads it, so a
    malformed one cannot refuse the call, and the bound values are laid over
    what is left.

    **A dropped value is logged at ``WARNING`` with the tool's name and the
    argument names, never the values**, which are the consumer's data. A
    withheld name is logged whenever the caller sent it; a bound one when the
    caller's value is not the one the server applies, compared by ``repr`` as
    a task worker replaying the arguments this already applied logs nothing a
    second time. One arc to coverage: without the withheld condition a withheld
    ``"null"`` goes unlogged
    (``test_the_bound_value_wins_and_the_withheld_one_is_dropped``), and without the comparison a caller restating the bound value is
    logged (``test_the_server_arguments_are_laid_without_a_second_warning`` and
    ``test_a_caller_restating_a_bound_value_with_another_is_logged_once``).

    A binding that binds nothing gets its arguments back untouched, the same
    object, without the kernel's rule being run
    (``test_a_tool_binding_nothing_never_reaches_the_kernels_rule``).
    """
    server = binding.server_arguments
    if not server.names():
        return arguments
    applied = apply_server_arguments(binding.spec, arguments, server)
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
    """A binding's scope gate judged against ``token``: ``(allowed, required_scopes)``.

    ``scopes`` is a binding's normalised ``scopes`` (``registry.types.utils.scope_names``),
    ``None`` for no gate. Refused when the token lacks any of them, and then
    ``required_scopes`` is every declared scope, not only the missing ones, so
    the ``WWW-Authenticate`` challenge names the whole set a fresh token needs.
    """
    # No gate is the empty set, which every token carries; registration
    # refuses ``scopes=()``, so the two never need telling apart here.
    declared = scopes or ()
    if token.has_all_scopes(declared):
        return True, []
    return False, list(declared)


def judge_tool_permissions(
    binding: KernelToolBinding, context: MCPCallContext
) -> tuple[bool, list[str]]:
    """A tool binding's scope gate, judged for this call.

    Every check a ``tools/call`` makes of ``binding.scopes`` goes through
    this: the sync and async handlers, the streamed call's pre-flight and the
    check made before a task is created (``maybe_create_task``). The spec's own
    checks are the kernel's, asked after it (``admit_kernel_tool_call``).
    """
    return scope_verdict(binding.scopes, context.token)


def forbidden(required_scopes: list[str]) -> JsonRpcError:
    """The ``-32006`` for a caller a binding's ``permissions`` deny.

    Every route answers the scope gate this way: both ``tools/call`` handlers,
    the streamed call's pre-flight and a task's creation.
    """
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
    """Run every rate limiter in order, returning the largest retry-after.

    Each limiter's ``consume`` updates its quota atomically and returns the
    suggested retry-after-seconds, or ``None`` to allow. The first denial stops
    the loop, so several limits per binding read as "deny if any is exhausted".

    An ``async def consume`` is refused rather than run: a coroutine is not
    ``None``, so it would deny every call with the coroutine object standing in
    for the retry-after seconds.
    """
    for limiter in rate_limits:
        if not isinstance(limiter, MCPRateLimit):  # defensive — caught at registration
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
    """The error every method answers when ``consume_rate_limits`` denied it,
    with the seconds to wait as ``retryAfter``."""
    return JsonRpcError(
        JsonRpcErrorCode.RATE_LIMITED,
        "Rate limit exceeded",
        data={"retryAfter": retry_after},
    )


def effective_rate_limits(binding: Any, context: MCPCallContext) -> tuple[Any, ...]:
    """A tool binding's rate limiters, or none when this dispatch must not charge.

    Nothing is charged for a task worker replaying a call whose limits the client
    already consumed; see ``MCPCallContext.enforce_rate_limits``. Only the tool
    paths consult this — resources, prompts and completions have no task
    equivalent.
    """
    return binding.rate_limits if context.enforce_rate_limits else ()


def validation_error_data(detail: Any, value: Any, *, include_value: bool) -> dict[str, Any]:
    """Build the ``detail`` / ``value`` pair a validation failure reports.

    Merged into the ``error`` object of a ``validation_error`` tool result (see
    ``validation_error_result``). Always carries the per-field ``detail``
    shape: a mapping of field name to a list of messages. ``include_value`` (the server's
    ``MCPConfig.include_validation_value``) also echoes ``value`` back; off by
    default because it may carry PII or secrets that must not flow back to the
    client or into client-side logs.
    """
    payload: dict[str, Any] = {"detail": detail}
    if include_value:
        payload["value"] = value
    return payload


def render_convention(template: str | None) -> str | None:
    """One placeholder-free ``AgentConventions`` field as the model reads it.

    Every field is a ``str.format`` template, rendered whether or not it has a
    placeholder, so a doubled brace is one brace in all of them alike -- the
    Pydantic-AI toolset's rule -- rather than reaching a model doubled from the
    three fields that take no placeholder
    (``test_a_doubled_brace_reaches_the_model_as_one_in_every_field``). ``None``
    is a dropped sentence and stays ``None``. The template was validated against
    this exact call when the conventions were built, so this cannot raise.
    ``missing_arguments``, the one field with a placeholder, is rendered where
    its names are known, in ``validation_error_result``.
    """
    return None if template is None else template.format()


def missing_arguments_message(names: Iterable[str], conventions: AgentConventions) -> str:
    """The server's ``missing_arguments`` sentence for a call that left ``names`` out.

    Each in backticks, joined with ", ", in the sorted order the kernel's
    ``InvalidArguments.missing`` gives them: the Pydantic-AI toolset's format,
    so one omission reads the same on both transports
    (``test_several_missing_names_are_sorted_and_joined``).
    """
    listed = ", ".join(f"`{name}`" for name in names)
    return conventions.missing_arguments.format(names=listed)


def validation_error_result(
    exc: ServiceValidationError,
    arguments: Any,
    *,
    config: MCPConfig,
) -> ToolResult:
    """The ``isError`` result for a tool call whose arguments a service refused.

    The MCP spec's tools "Error Handling" section files "input validation
    errors (e.g., date in wrong format, value out of range)" under tool
    execution errors, reported with ``isError: true`` so the model can read
    them and correct its call; a JSON-RPC protocol error is for an unknown tool
    or a request that fails the ``CallToolRequest`` schema. The same rule holds
    in every served protocol version: 2025-06-18 lists "invalid input data"
    under tool execution errors too.

    The ``error`` object carries ``type: "validation_error"``, the service's
    message, and what ``validation_error_data`` builds: ``detail`` keyed by
    field, and ``value`` only under ``INCLUDE_VALIDATION_VALUE``. The kernel's
    own argument refusals are worded in ``kernel_refusals``, which serves them
    in the same shape.
    """
    return build_error_tool_result(
        exc.message,
        error_type="validation_error",
        detail=validation_error_data(
            exc.detail, arguments, include_value=config.include_validation_value
        ),
    )


def service_error_result(exc: ServiceError) -> ToolResult:
    """The ``isError`` tool result for a ``ServiceError`` a dispatch raised.

    ``type`` stays ``"service_error"`` for every member of the family, because
    that is what an existing client branches on. An
    ``ActionUnavailable`` -- a declared affordance refusing the call -- also carries its ``code``: the
    message is the affordance's ``reason``, a sentence that gets reworded, and the
    code is the stable name a client switches on. The kernel asks a transport
    serving an agent to pass on both, and every arm once passed on only the
    sentence.

    The key is **absent**, not ``null``, for any other ``ServiceError``. A
    ``ServiceConflict`` raised by hand from a precondition has no code and never
    will, which is why the kernel made the code a subclass field rather than a
    nullable one on the parent; a ``null`` here would reintroduce the field that
    lies at every other call site.

    Every ``ServiceError`` arm builds its result here -- the kernel tool's sync
    and async runs and the in-process ``call_tool`` -- so a refusal answers the
    same way whichever path served it.
    """
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
    """The ``validation_error`` result for a refusal of ``supplied`` while rendering.

    ``supplied`` is the caller's arguments the render read: the tool's
    ``shapes`` the call carried (``present_kernel_result``). Empty, ``exc`` is
    raised unchanged, because nothing the caller sent shaped the render and the
    refusal is a server bug.
    """
    # One branch arc to coverage, so the gate cannot see it removed. Held by
    # tests/handlers/test_kernel_presenter_refusal.py's
    # ``test_a_refusal_with_no_shape_supplied_is_raised``, which fails when this
    # guard is deleted.
    if not supplied:
        raise exc
    detail: Any = exc.detail
    key: str = supplied[0] if len(supplied) == 1 else "non_field_errors"
    message: str = (
        f"{_name_list(supplied)} was rejected while rendering the result: "
        f"{_readable_detail(detail)}"
    )
    # One branch arc for two conditions, so each is named by the test that fails
    # without it: ``test_a_refused_shape_is_a_validation_error_keyed_by_it``, on an unpaged tool
    # (``paginated``) and
    # ``test_none_drops_the_handle_wording_and_the_scope_sentence`` (the
    # ``None`` check, which would otherwise append the text "None").
    scope = render_convention(conventions.query_param_on_pages)
    if paginated and scope is not None:
        message = f"{message} {scope}"
    return build_error_tool_result(
        message,
        error_type="validation_error",
        detail=validation_error_data(
            # Normalised to the per-field shape: a ``ServiceValidationError``
            # may carry a bare string, and a field's errors are always a list.
            {key: detail if isinstance(detail, (list, dict)) else [detail]},
            arguments,
            include_value=config.include_validation_value,
        ),
    )


def _name_list(names: list[str]) -> str:
    """``names`` as prose: "`a`", or "`a`, `b` or `c`" when there are several.

    "Or" rather than "and", because the error came from one render that read all
    of them, and which one it refused is not something the error says.
    """
    quoted: list[str] = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _readable_detail(detail: Any) -> str:
    """A validation ``detail`` as sentences a model reads, not as a ``repr``.

    A ``ServiceValidationError``'s detail is a string, a list of them, or a
    mapping of field name to either; the ``repr`` of any of those is what an
    unhandled render error would put in front of the model.
    """
    if isinstance(detail, dict):
        return " ".join(f"`{key}`: {_readable_detail(value)}" for key, value in detail.items())
    if isinstance(detail, list):
        return " ".join(_readable_detail(item) for item in detail)
    return end_sentence(str(detail))


def resolve_bound(override: Any, default: Any) -> Any:
    """Resolve a per-binding outbound bound against the server's default.

    ``UNSET`` means the binding said nothing; any other value — including
    ``None``, meaning *no ceiling* — is the binding's deliberate answer and wins.
    All three bounds need this shape because a ``None``-is-default idiom would
    make "no ceiling for this one tool" inexpressible.
    """
    return default if isinstance(override, UnsetType) else override


def resource_not_found_code(protocol_version: str) -> JsonRpcErrorCode:
    """Which code a missing ``resources/read`` target gets, by era.

    The one place the two eras disagree on a wire value. ``2025-11-25`` names
    ``-32002`` for "Resource not found"; ``2026-07-28`` retired it for
    ``-32602`` while telling clients to keep *recognising* the old one, so
    neither value is safe to emit to both.
    """
    if protocol_version in MODERN_PROTOCOL_VERSIONS:
        return JsonRpcErrorCode.INVALID_PARAMS
    return JsonRpcErrorCode.RESOURCE_NOT_FOUND


def catalog_cache_hints(*, ttl_ms: int, per_caller: bool) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a catalog result.

    Covers ``server/discover`` and the four list methods. ``cacheScope`` is
    derived, not configured: ``per_caller`` says whether anything about this
    caller shaped the result. ``FILTER_LISTINGS_BY_PERMISSIONS`` does for every
    list, since a listing is then a function of the caller's permissions;
    ``tools/list`` also does whenever it asked an operation-scope affordance,
    which is answered against the caller's ``user`` and ``request``. Either way
    ``public`` would licence a shared proxy to serve one tenant's listing to
    another.
    """
    scope = CacheScope.PRIVATE if per_caller else CacheScope.PUBLIC
    return {"ttlMs": ttl_ms, "cacheScope": scope.value}


def resource_cache_hints(ttl_ms: int) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a ``resources/read`` result.

    Always ``private``: the body is whatever the binding's selector produced for
    *this* caller, so sharing it across authorization contexts is never correct.
    The TTL is the only knob.
    """
    return {"ttlMs": ttl_ms, "cacheScope": CacheScope.PRIVATE.value}


def enforce_result_ceiling(result: Any, *, max_result_bytes: int | None, label: str) -> Any:
    """Replace an over-ceiling tool result with an ``isError`` result.

    Applied once per handler, to the finished result, so every dispatch path is covered
    by one check that sees what goes on the wire. A
    [`JsonRpcError`][django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError]
    passes through — rewriting it would change the envelope the client awaits."""
    if isinstance(result, JsonRpcError):
        return result
    message: str | None = enforce_result_bytes(result, max_result_bytes, label=label)
    if message is None:
        return result
    return build_error_tool_result(message, error_type="result_too_large").to_dict()


async def run_with_deadline(coro: Awaitable[Any], seconds: float | None) -> Any:
    """Await ``coro``, raising ``asyncio.TimeoutError`` past ``seconds``.

    ``None`` awaits without a deadline, so callers can hand the resolved bound
    straight in.

    **This does not stop the work.** ``wait_for`` cancels the *task*, and a task
    parked in ``sync_to_async`` — where every ORM-backed spec spends its time —
    waits on a thread asyncio cannot interrupt. The deadline buys the client a
    terminal response, not a stopped query; pair it with a database statement
    timeout. ``asyncio.wait_for`` rather than ``asyncio.timeout``, which is 3.11+.
    """
    if seconds is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=seconds)


def span_attrs(binding_name: str, context: MCPCallContext) -> dict[str, Any]:
    """The span attributes every handler that opens a span sets, so they cannot
    drift apart: the binding's name, the protocol version and, once a session
    exists, its id."""
    attrs: dict[str, Any] = {
        "mcp.binding.name": binding_name,
        "mcp.protocol.version": context.protocol_version,
    }
    if context.session_id:
        attrs["mcp.session.id"] = context.session_id
    return attrs


__all__ = [
    "apply_tool_server_arguments",
    "consume_rate_limits",
    "effective_rate_limits",
    "enforce_result_ceiling",
    "judge_tool_permissions",
    "missing_arguments_message",
    "rate_limited",
    "render_convention",
    "rendering_refusal_result",
    "resolve_bound",
    "run_with_deadline",
    "scope_verdict",
    "span_attrs",
    "validation_error_data",
    "validation_error_result",
]
