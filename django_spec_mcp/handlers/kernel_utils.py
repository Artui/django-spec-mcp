"""Helpers the kernel dispatch path shares across its sync, async and in-process callers.

Kept apart from ``handlers/utils.py``, which serves the drf-services path, so
the two paths share only the helpers they call by name.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import islice
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django_service_specs import (
    DEFAULT_PAGE_SIZE,
    DispatchError,
    DispatchResult,
    Grant,
    OutputPage,
    SelectorKind,
    ServiceError,
    is_queryset,
    paginate_output,
    present_for_audience,
    spec_output_schema,
)

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.handlers.utils import (
    resolve_bound,
    resource_cache_hints,
    resource_not_found_code,
)
from django_spec_mcp.observability import get_logger
from django_spec_mcp.output.encode_json import encode_json
from django_spec_mcp.output.enforce_result_bytes import enforce_result_bytes
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.output.resolve_structured_output import resolve_structured_output
from django_spec_mcp.output.tool_result import build_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.resource_contents import ResourceContents
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.output_format import OutputFormat

logger = get_logger(__name__)

# The two arguments a paginated ``LIST`` tool takes for its page, which the
# kernel's argument check would refuse as undeclared if they reached dispatch.
_PAGE_ARGUMENTS: frozenset[str] = frozenset({"page", "limit"})


@dataclass(frozen=True)
class KernelPageRequest:
    """The page a paginated ``LIST`` tool was asked for, before clamping."""

    page: int
    limit: int
    max_page_size: int | None


def call_context(request: Any, action: str) -> dict[str, Any]:
    """The ``call_context`` one kernel dispatch receives.

    One dict per call, built fresh so nothing a spec writes into it reaches
    another call. ``request`` is the transport's ``HttpRequest``, carrying the
    resolved token as ``request.mcp_token``, or ``None`` off HTTP; there is no
    view and no format negotiation on this transport, and ``action`` is the
    tool or resource name, the role ``view.action`` plays for a view.
    """
    return {"request": request, "view": None, "format": None, "action": action}


def principal_of(user: Any) -> Any:
    """The principal a kernel dispatch runs for: ``user``, or an ``AnonymousUser``.

    ``acall_tool`` / ``call_tool`` take ``user=None`` for an anonymous caller,
    and the kernel's ``adispatch`` reads ``principal=None`` as no principal
    given at all, refusing the call with a ``TypeError``. An ``AnonymousUser``
    is what an anonymous request carries over HTTP, so both routes and both
    dispatchers see the same principal
    (``test_an_anonymous_in_process_caller_reaches_a_kernel_tool``).
    """
    return AnonymousUser() if user is None else user


def unguarded_grant(binding: Any, principal: Any, config: MCPConfig) -> Grant | None:
    """The ``Grant`` an unguarded kernel binding is dispatched under, or ``None``.

    Only a binding the registrar marked ``unguarded`` gets one: ``register_tool``
    and ``register_resource`` mark a spec declaring ``permissions=None`` when
    they register it with ``REQUIRE_TOOL_PERMISSIONS`` off
    (``check_kernel_spec_guarded``), and it is then called without a permission
    check. ``register_tools`` never marks one, since the kernel's
    ``SpecRegistry`` it reads refuses such a spec when it is built
    (``test_a_spec_registry_cannot_carry_an_unguarded_spec_to_register_tools``). The kernel's dispatch refuses such a spec on its own, so
    the call carries a grant bound to this spec and ``principal``, which must be
    the very principal the call dispatches with.

    The mark is read rather than ``spec.permissions``, so a binding built by hand
    and passed straight to ``server.tools.register`` or
    ``server.resources.register`` never gets a grant, whatever the setting, and
    the kernel refuses its unguarded spec at dispatch
    (``test_a_handbuilt_unguarded_kernel_tool_is_never_called``). A guarded spec
    is never marked, so its checks always run
    (``test_a_guarded_kernel_tool_still_refuses_with_the_setting_off``).

    The setting is read again at the call, from the server answering it: a
    binding one server marked and another server, with the setting on, holds
    in its registry is not granted there
    (``test_a_marked_binding_is_not_granted_by_a_server_with_the_setting_on``).
    """
    if binding.unguarded and not config.require_tool_permissions:
        return Grant(binding.spec, principal)
    return None


def carry_token(request: Any, token: Any) -> Any:
    """Hang the resolved ``TokenInfo`` on ``request`` as ``mcp_token``, and return it.

    A spec reading the caller's token (its scopes, its audience) reads it off
    the ``request`` in its ``call_context``, which is where a permission check
    over HTTP finds it. Only the wire calls this: off HTTP there is neither a
    request nor a token to carry.
    """
    request.mcp_token = token
    return request


def page_request(
    binding: KernelToolBinding, arguments: dict[str, Any], config: MCPConfig
) -> tuple[dict[str, Any], KernelPageRequest | None]:
    """Split a paginated ``LIST`` tool's ``page`` / ``limit`` off its arguments.

    Every other binding's arguments are returned whole, with no page. The clamps
    and the envelope arithmetic are ``paginate_output``'s; this only reads the
    two untyped values as ints, falling back to the first page and the kernel's
    default size, as the drf-services path's ``_coerce_int`` does.
    """
    if not binding.paginate:
        return arguments, None
    remaining = {name: value for name, value in arguments.items() if name not in _PAGE_ARGUMENTS}
    return remaining, KernelPageRequest(
        page=_coerce_int(arguments.get("page"), default=1),
        limit=_coerce_int(arguments.get("limit"), default=DEFAULT_PAGE_SIZE),
        max_page_size=resolve_bound(binding.max_page_size, config.max_page_size),
    )


def present_tool_result(
    binding: KernelToolBinding, result: DispatchResult, page: KernelPageRequest | None
) -> Any:
    """``result`` presented for an agent, as one page when the tool paginates.

    Synchronous, because a presenter may read related rows; the async caller
    runs it off the event loop. The audience projection lands on the rows, and
    the page envelope is laid around what they present as.

    A list-shaped spec that presents nothing is served the empty list, which
    its ``outputSchema`` advertises as ``"array"`` (``kernel_tool_output_schema``),
    rather than the ``{}`` a ``None`` becomes: the result drf-services' ``many``
    serves for the same declaration
    (``test_a_list_that_presents_nothing_conforms_to_its_schema``).
    """
    if page is None:
        presented = present_for_audience(
            binding.spec, result, projection=binding.audience_projection
        )
        if presented is None and _presents_a_list(binding):
            return []
        return presented
    output: OutputPage = paginate_output(
        result.value, page=page.page, limit=page.limit, max_page_size=page.max_page_size
    )
    rows = present_for_audience(
        binding.spec, replace(result, value=output.items), projection=binding.audience_projection
    )
    return output.envelope(rows)


def _presents_a_list(binding: KernelToolBinding) -> bool:
    """Whether the kernel describes what ``binding`` presents as a list or nothing.

    The root ``kernel_tool_output_schema`` restates as ``"array"``: a ``many`` or
    ``returns_many`` service's rows, or ``None``.
    """
    # A spec with no presenter has no output schema, and presents nothing at all
    # (``test_kernel_shell_knobs``). The type keeps a single row that presents
    # nothing serving ``{}`` (``test_a_result_that_presents_nothing_conforms_to_its_schema``).
    schema = spec_output_schema(binding.spec)
    return schema is not None and schema.get("type") == ["array", "null"]


def bound_unpaginated_rows(
    binding: KernelToolBinding,
    result: DispatchResult,
    page: KernelPageRequest | None,
    config: MCPConfig,
) -> DispatchResult | ToolResult:
    """``result`` with an unpaginated ``LIST`` tool's rows taken up to the row ceiling.

    The ceiling is the most rows one ``LIST`` tool result carries, paged or not:
    the binding's ``max_page_size``, deferring to the server's. A page clamps to
    it inside ``paginate_output`` and says so in its envelope; an unpaginated
    result carries nothing that could say rows were dropped, so one over the
    ceiling is refused as ``result_too_large`` rather than truncated, as the
    byte ceiling refuses
    (``test_an_unpaginated_list_over_the_ceiling_is_refused``).

    One row past the ceiling is read, so "exactly at the ceiling" is told from
    "over it", and read as a **slice**, so a QuerySet bounds the fetch in SQL
    and its result cache is what the presenter then iterates
    (``test_the_ceiling_bounds_the_fetch_and_not_just_the_payload``). That is
    the point of doing this before presenting rather than leaning on the byte
    ceiling, which measures a payload already fetched and presented in full.
    Synchronous, because the slice runs the query; the async caller runs it off
    the event loop.

    Every other result passes untouched. Each condition is held by a test: a
    page by ``test_a_model_supplied_limit_is_clamped_to_the_server_ceiling``, a
    tool that is no ``LIST`` by ``test_a_service_tool_returning_rows_is_not_row_bounded``
    and a disabled ceiling by ``test_a_disabled_ceiling_serves_an_unpaginated_list_whole``.
    """
    max_rows = resolve_bound(binding.max_page_size, config.max_page_size)
    if page is not None or binding.kind is not SelectorKind.LIST or max_rows is None:
        return result
    # The kernel hands a ``LIST`` selector's Manager over as its ``.all()``, and
    # presents a ``LIST`` by iterating what it returned, so every value here is
    # rows to iterate, a mapping's keys and a string's characters included
    # (``test_any_list_result_is_bounded_by_the_same_ceiling``).
    rows: Any = result.value
    # ``rows[:n]`` on a QuerySet is a LIMIT, and ``len()`` evaluates it once;
    # any other iterable, a list or a generator, is windowed with ``islice``.
    window: Any = rows[: max_rows + 1] if is_queryset(rows) else list(islice(rows, max_rows + 1))
    if len(window) > max_rows:
        return _over_row_ceiling(binding, max_rows)
    return replace(result, value=window)


def _over_row_ceiling(binding: KernelToolBinding, max_rows: int) -> ToolResult:
    """The refusal of an unpaginated result carrying more rows than the ceiling."""
    # The caller is told by the result; the operator only by this. A bound that
    # fires invisibly reads to everyone else as "the tool is broken".
    logger.warning(
        "Row bound exceeded: unpaginated tool %r resolved more than the %d row ceiling",
        binding.name,
        max_rows,
    )
    return build_error_tool_result(
        f"Tool {binding.name!r} is unpaginated and resolved more than this server's "
        f"{max_rows} row ceiling. Narrow the request (add or tighten a filter) and "
        "call again. The result was not truncated: an unpaginated payload carries "
        "nothing to say rows were dropped, so a partial one would look complete. "
        "Registering the tool with paginate=True lets it be read a page at a time.",
        error_type="result_too_large",
    )


def not_found_result(binding: KernelToolBinding) -> ToolResult:
    """A ``not_found`` dispatch result, worded as the drf-services path words it.

    A ``LIST`` has no instance to miss, so its wording is the selector tool's
    ``not found``; a ``RETRIEVE`` and a service tool's instance selector both
    miss an instance.
    """
    if binding.kind is SelectorKind.LIST:
        return build_error_tool_result(f"{binding.name}: not found", error_type="not_found")
    return build_error_tool_result(
        f"{binding.name}: no matching instance found", error_type="not_found"
    )


def tool_result(
    binding: KernelToolBinding, payload: Any, *, output_format: Any, config: MCPConfig
) -> ToolResult:
    """The successful ``tools/call`` result for ``payload``.

    ``output_format`` is the caller's ``outputFormat`` where the route takes
    one, falling back to the binding's.
    """
    _emit_output_schema, emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
        default_output_schema=config.include_output_schema,
        default_structured_content=config.include_structured_content,
    )
    return build_tool_result(
        payload,
        output_format=OutputFormat.coerce(output_format or binding.output_format),
        include_structured_content=emit_structured_content,
        content_kind=binding.content_kind,
        content_mime_type=binding.content_mime_type,
        binding_name=binding.name,
    )


def on_the_wire(
    answer: ToolResult | dict[str, Any] | JsonRpcError,
) -> dict[str, Any] | JsonRpcError:
    """An answer as a ``tools/call`` handler returns it: a tool result as its dict."""
    return answer.to_dict() if isinstance(answer, ToolResult) else answer


def resource_read_result(
    binding: KernelResourceBinding,
    uri: str,
    result: DispatchResult,
    payload: Any,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """The ``resources/read`` answer for a kernel resource's dispatch ``result``.

    A ``not_found`` is the resource-not-found error the protocol version names,
    with the URI in ``data``, as an unknown URI is answered. Otherwise the
    presented ``payload`` is the one JSON ``contents`` block, with the
    binding's ``_meta``, the cache hints and the server's result ceiling, as
    the drf-services read builds them.
    """
    if result.kind == "not_found":
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Resource not found: {uri!r}",
            data={"uri": uri},
        )
    contents = ResourceContents(
        uri=uri,
        mime_type=binding.mime_type,
        text=encode_json(payload),
        meta=dict(binding.meta) or None,
    )
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


def resource_refusal(exc: DispatchError | ServiceError, scene: KernelRefusalScene) -> JsonRpcError:
    """A kernel refusal on ``resources/read``, where there is no ``isError`` result.

    The map's answer when it is a JSON-RPC error, which is a denied or
    deactivated principal's ``-32006``. Any other refusal is raised on, to the
    transport's internal error, which is how the drf-services read answers a
    selector that raises.
    """
    answer = answer_kernel_refusal(exc, scene)
    if isinstance(answer, JsonRpcError):
        return answer
    raise exc


def _coerce_int(value: Any, *, default: int) -> int:
    """``value`` as an int, or ``default`` for anything that is not one.

    Stands in for ``selector_tool_dispatch._coerce_int``, which is private to the
    drf-services path: a bool is refused, since ``True`` is an ``int``, and a
    numeric string is read.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


__all__ = [
    "KernelPageRequest",
    "bound_unpaginated_rows",
    "call_context",
    "carry_token",
    "not_found_result",
    "on_the_wire",
    "page_request",
    "principal_of",
    "present_tool_result",
    "resource_read_result",
    "resource_refusal",
    "tool_result",
    "unguarded_grant",
]
