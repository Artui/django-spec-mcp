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


def apply_tool_server_arguments(binding: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """``arguments`` with the binding's ``server_arguments`` in place, the caller's dropped.

    The first thing every ``tools/call`` route does with the arguments, once it
    knows they are an object: ``resolve_tool_call``, for both transports,
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


def resolve_tool_call(
    params: dict[str, Any], context: MCPCallContext
) -> tuple[KernelToolBinding, dict[str, Any]] | JsonRpcError:
    """The ``tools/call`` request both transports start from: the binding it
    names and its arguments with the binding's ``server_arguments`` applied, or
    the protocol error a malformed request or an unknown tool gets. The caller
    checks that ``params`` is an object, since it reads them again after."""
    tool_name: Any = params.get("name")
    if not isinstance(tool_name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.tools.get(tool_name)
    if binding is None:
        # The tools spec's own worked example of a protocol error: an unknown
        # tool is a bad param, so ``-32602`` rather than an ``isError`` result.
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown tool: {tool_name!r}")

    # Only a missing key (or an explicit ``null``) means "no arguments". Not
    # ``or {}``: folding every falsy value into ``{}`` would let ``[]``, ``0``,
    # ``""`` and ``false`` past the guard on the next line, so a malformed
    # ``arguments`` would run the tool with no arguments instead of being named
    # as the fault.
    arguments_raw: Any = params.get("arguments")
    if arguments_raw is None:
        arguments_raw = {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    # The binding's ``server_arguments`` in place before anything reads the
    # arguments: a task records what the tool will run with, and the permission
    # check at its creation, the dispatch and the strict split and
    # missing-argument checks all see the server's value, so a binding can fill
    # a required read (``apply_tool_server_arguments``).
    return binding, apply_tool_server_arguments(binding, arguments_raw)


def resolve_prompt_get(
    params: dict[str, Any] | None, context: MCPCallContext
) -> tuple[PromptBinding, dict[str, Any]] | JsonRpcError:
    """The ``prompts/get`` request both transports start from: the binding it
    names and the client's arguments, or the protocol error a malformed
    request, an unknown prompt or a missing required argument gets."""
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "prompts/get params must be an object")
    name: Any = params.get("name")
    if not isinstance(name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.prompts.get(name)
    if binding is None:
        # The prompts spec is explicit: "Invalid prompt name: -32602". An
        # unknown name is a fault in the *params*, and ``-32002`` is reserved
        # for ``resources/read``.
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
    return binding, arguments_raw


def prompt_get_answer(
    binding: PromptBinding, raw: Any, context: MCPCallContext
) -> dict[str, Any] | JsonRpcError:
    """The ``prompts/get`` answer for what a prompt's ``render`` returned,
    normalised into messages by ``normalize_render_result``."""
    try:
        messages = normalize_render_result(raw)
    except TypeError as exc:
        return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc))

    result: dict[str, Any] = GetPromptResult(
        messages=messages, description=binding.description
    ).to_dict()
    # The same outbound ceiling ``tools/call`` and ``resources/read``
    # apply. A rendered prompt is a result surface like any other — its
    # body is whatever a consumer's ``render`` interpolated — so an
    # operator who set the ceiling gets it here too. No ``isError``
    # envelope exists on this method, so an over-ceiling render is a
    # protocol error carrying the same remedy-naming message.
    oversize: str | None = enforce_result_bytes(
        result, context.config.max_result_bytes, label=f"Prompt {binding.name!r}"
    )
    if oversize is not None:
        return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
    return result


def resolve_resource_read(
    params: dict[str, Any] | None, context: MCPCallContext
) -> tuple[ResourceBinding[Any], dict[str, str], str] | JsonRpcError:
    """The ``resources/read`` request both transports start from: the binding
    its URI resolves to, the URI-template variables and the URI, or the
    protocol error a malformed request or an unknown URI gets."""
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
    return binding, vars_, uri


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


def catalog_cursor(params: dict[str, Any] | None) -> str | None | JsonRpcError:
    """The opaque ``cursor`` a list method was sent, ``None`` for the first
    page, or the protocol error a cursor that is not a string gets."""
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
    """One page of a list method's ``bindings``, each rendered by ``entry``
    under ``key``, with the catalog cache hints and a ``nextCursor`` when the
    page does not reach the end. ``bindings`` arrive already filtered, so the
    cursor walks the slice this caller sees. A malformed cursor is
    ``-32602``."""
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
    """``ttlMs`` / ``cacheScope`` for a ``resources/read`` result.

    Always ``private``: the body is whatever the binding's selector produced for
    *this* caller, so sharing it across authorization contexts is never correct.
    The TTL is the only knob.
    """
    return {"ttlMs": ttl_ms, "cacheScope": CacheScope.PRIVATE.value}


def resource_read_answer(
    contents: ResourceContents, binding: ResourceBinding[Any], uri: str, context: MCPCallContext
) -> dict[str, Any] | JsonRpcError:
    """The ``resources/read`` answer around one encoded ``contents`` block, the
    step a view's read and a kernel resource's read share: the binding's cache
    hints, then the server's result ceiling. A resource read has no ``isError``
    envelope to carry an over-ceiling explanation, so it is a protocol error
    carrying the same remedy-naming message a tool result gets."""
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
