from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import islice
from types import MappingProxyType
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_PAGE_SIZE,
    CallSeeds,
    DispatchError,
    DispatchResult,
    FileResult,
    FileTooLarge,
    Grant,
    InvalidArguments,
    NotPermitted,
    OutputPage,
    PoolSeeds,
    PrincipalUnavailable,
    SelectorKind,
    ServiceError,
    UnknownArguments,
    authorize_without_arguments,
    build_call_context,
    check_call,
    coerce_flat,
    is_queryset,
    paginate_output,
    present_for_audience,
    server_owned_keys,
    spec_output_schema,
)

from django_spec_mcp.config.types.mcp_config import MCPConfig
from django_spec_mcp.elicitation.types.resolved_input import ResolvedInput
from django_spec_mcp.handlers.answer_kernel_refusal import answer_kernel_refusal
from django_spec_mcp.handlers.answer_mapped_exception import answer_mapped_exception
from django_spec_mcp.handlers.exception_handler_for import exception_handler_for
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.handlers.types.kernel_input_round import KernelInputRound
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.handlers.utils import (
    resolve_bound,
    resource_not_found_code,
    resource_read_answer,
    validation_error_data,
)
from django_spec_mcp.observability import get_logger
from django_spec_mcp.output.encode_resource_contents import encode_resource_contents
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.output.resolve_structured_output import resolve_structured_output
from django_spec_mcp.output.tool_result import build_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.file_content_kinds import FILE_CONTENT_KINDS
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.mcp_transport import MCP_TRANSPORT

logger = get_logger(__name__)

# The two arguments a paginated ``LIST`` tool takes for its page, which the
# kernel's argument check would refuse as undeclared if they reached dispatch.
_PAGE_ARGUMENTS: frozenset[str] = frozenset({"page", "limit"})

# What a kernel resource's wrong-typed body is blamed on: ``present`` hands the
# presenter's answer through unchanged, or the selector's value when the spec
# declares no presenter, so one of the two returned it.
_PRESENTED_BY: str = "the spec's presenter (or its selector, when it declares none)"


# The two refusals the kernel's prelude answers before the call is anyone's to
# charge: a principal its checks refuse, and one with no active account. Both
# are ``-32006`` in ``KERNEL_REFUSALS``. One arc to coverage at every
# ``except``: ``PrincipalUnavailable`` is held by
# ``test_a_deactivated_principal_is_not_charged`` and
# ``test_a_deactivated_reader_is_not_charged``, ``NotPermitted`` by
# ``test_a_caller_the_spec_refuses_is_not_charged``.
_DENIALS: tuple[type[DispatchError], ...] = (NotPermitted, PrincipalUnavailable)


@dataclass(frozen=True)
class KernelAdmission:
    """A kernel call its spec's class-level checks admitted, as ``dispatch`` is to run it."""

    principal: Any
    grant: Grant | None
    call_context: dict[str, Any]
    seeds: CallSeeds | None


@dataclass(frozen=True)
class KernelPageRequest:
    """The page a paginated ``LIST`` tool was asked for, before clamping."""

    page: int
    limit: int
    max_page_size: int | None


def call_context(request: Any, action: str) -> dict[str, Any]:
    """The ``call_context`` one kernel dispatch receives."""
    return build_call_context(MCP_TRANSPORT, request=request, action=action)


def tool_call_context(binding: Any, context: MCPCallContext) -> dict[str, Any]:
    """The ``call_context`` a ``tools/call`` over the wire runs ``binding`` under."""
    return call_context(carry_token(context.http_request, context.token), binding.name)


def principal_of(user: Any) -> Any:
    """The principal a kernel dispatch runs for: ``user``, or an ``AnonymousUser``."""
    return AnonymousUser() if user is None else user


def unguarded_grant(binding: Any, principal: Any, config: MCPConfig) -> Grant | None:
    """The ``Grant`` an unguarded kernel binding is dispatched under, or ``None``."""
    if binding.unguarded and not config.require_tool_permissions:
        return Grant(binding.spec, principal)
    return None


def carry_token(request: Any, token: Any) -> Any:
    """Make ``request`` the caller's: the token on it, and its principal as the user."""
    request.mcp_token = token
    return seat(request, principal_of(token.user))


def seat(request: Any, principal: Any) -> Any:
    """Set ``principal`` as ``request``'s user, for its sync and async readers both."""

    async def auser() -> Any:
        return principal

    request.user = principal
    request.auser = auser
    return request


# What a handed request's body and parse state are stored under, which a copy
# reads and writes on the handed request rather than holding its own: the
# names ``HttpRequest`` reads and writes on ``self`` from Django 4.2 to 6.1, in
# ``body``, ``read``, ``_load_post_and_files`` and the ``encoding``,
# ``upload_handlers`` and ``multipart_parser_class`` setters, which a method
# override on the copy could not reach. The configuration names go with the
# parse they configure: the ``encoding`` setter discards the parsed ``POST``
# and the decoded ``GET``, so ``GET`` is shared with it, and the other two
# refuse once ``FILES`` is parsed, so a copy configuring itself alone would
# discard or refuse on the caller's parse. ``ASGIRequest``'s
# ``_post_parse_error`` is set in ``__init__`` and read by nothing, so it is
# not here. The test holding each name is on ``copy_request``.
_REQUEST_SHARES: frozenset[str] = frozenset(
    {
        "_body",
        "_stream",
        "_read_started",
        "_post",
        "_files",
        "_encoding",
        "GET",
        "_upload_handlers",
        "_multipart_parser_class",
    }
)

# DRF's ``Request`` parse cache, the same way: what ``data``, ``POST``,
# ``FILES`` and ``stream`` cache under these names, so a spec parsing through a
# copy leaves the parse on the caller's wrapper rather than a consumed stream
# for it to parse again. ``_content_type`` is never written by a parse: only
# ``__init__`` writes it, and DRF's ``clone_request`` on the new request it
# builds, so a copy never holds a value of its own for it, and it is not here.
_WRAPPER_SHARES: frozenset[str] = frozenset({"_data", "_files", "_full_data", "_stream"})


class _SharesBodyState:
    """The base of a copy ``copy_request`` makes, sharing names with the handed request."""

    _shared_names: frozenset[str]

    def __getattribute__(self, name: str) -> Any:
        return _holder(self, name).__getattribute__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self)._shared_names and isinstance(value, _SharesBodyState):
            value = object.__getattribute__(value, "_shares_with")
        _holder(self, name).__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        _holder(self, name).__delattr__(name)

    def _initialize_handlers(self) -> None:
        """Initialise the handed request's upload handlers, bound to it rather than to the copy."""
        object.__getattribute__(self, "_shares_with")._initialize_handlers()


def _holder(clone: _SharesBodyState, name: str) -> Any:
    """What ``name`` is read on for ``clone``: the handed request, or the copy itself."""
    if name in type(clone)._shared_names:
        return object.__getattribute__(clone, "_shares_with")
    return super(_SharesBodyState, clone)


def _sharing_class(
    cls: type, shared: frozenset[str], classes: dict[tuple[type, frozenset[str]], type]
) -> type:
    """A subclass of ``cls`` whose instances share ``shared`` with the request they copy."""
    made = classes.get((cls, shared))
    if made is None:
        namespace = {
            "__module__": cls.__module__,
            "__qualname__": cls.__qualname__,
            "_shared_names": shared,
        }
        made = classes.setdefault(
            (cls, shared), type(cls.__name__, (_SharesBodyState, cls), namespace)
        )
    return made


def copy_request(request: Any, classes: dict[tuple[type, frozenset[str]], type]) -> Any:
    """A copy of ``request`` for one in-process call to write its user to."""
    if isinstance(request, _SharesBodyState):
        cls: type = type(request)
        shared = type(request)._shared_names
        source = object.__getattribute__(request, "_shares_with")
    else:
        shared = _WRAPPER_SHARES if "_request" in request.__dict__ else _REQUEST_SHARES
        cls = _sharing_class(type(request), shared, classes)
        source = request
    clone: Any = object.__new__(cls)
    own = request.__dict__.copy()
    vars(clone).update((name, value) for name, value in own.items() if name not in shared)
    object.__setattr__(clone, "_shares_with", source)
    if "_request" in own:
        clone._request = copy_request(clone._request, classes)
    return clone


def admits_without_arguments(
    binding: KernelToolBinding | KernelResourceBinding,
    principal: Any,
    *,
    config: MCPConfig,
    pool_seeds: PoolSeeds,
    call_context: dict[str, Any],
) -> bool:
    """Whether ``binding``'s spec lets ``principal`` past its checks, asked with no arguments."""
    grant = unguarded_grant(binding, principal, config)
    if grant is None and binding.spec.permissions is None:
        return False
    try:
        authorize_without_arguments(
            binding.spec,
            principal,
            grant=grant,
            call_context=call_context,
            pool_seeds=pool_seeds,
        )
    except ImproperlyConfigured:
        raise
    except Exception:
        return False
    return True


def admit_kernel_call(
    binding: KernelToolBinding | KernelResourceBinding,
    arguments: dict[str, Any],
    *,
    user: Any,
    config: MCPConfig,
    pool_seeds: PoolSeeds,
    unknown_arguments: UnknownArguments,
    context: dict[str, Any],
    exception_map: Mapping[type[Exception], ExceptionHandler] = MappingProxyType({}),
) -> KernelAdmission:
    """Ask a kernel spec's class-level checks about a call before the transport acts on it."""
    principal: Any = principal_of(user)
    unchecked = unguarded_grant(binding, principal, config)
    keywords: dict[str, Any] = {"pool_seeds": pool_seeds, "call_context": context}
    grant: Grant | None
    seeds: CallSeeds | None
    try:
        checked = check_call(
            binding.spec,
            principal=principal,
            arguments=arguments,
            grant=unchecked,
            unknown_arguments=unknown_arguments,
            **keywords,
        )
        _arguments, grant = checked
        seeds = checked.seeds
    except _DENIALS:
        raise
    except InvalidArguments as refusal:
        if not refusal.after_authorize and not admits_without_arguments(
            binding, principal, config=config, pool_seeds=pool_seeds, call_context=context
        ):
            raise NotPermitted() from None
        grant, seeds = unchecked, None
    except (DispatchError, ServiceError):
        grant, seeds = unchecked, None
    except Exception as exc:
        if exception_handler_for(exc, exception_map) is None:
            raise
        grant, seeds = unchecked, None
    return KernelAdmission(principal=principal, grant=grant, call_context=context, seeds=seeds)


def admit_kernel_tool_call(
    binding: KernelToolBinding,
    arguments: dict[str, Any],
    context: MCPCallContext,
    dispatch_context: dict[str, Any],
) -> KernelAdmission | JsonRpcError:
    """``admit_kernel_call`` for a ``tools/call`` over the wire, a denial as its answer."""
    dispatched, _page = page_request(binding, arguments, context.config)
    try:
        return admit_kernel_call(
            binding,
            dispatched,
            user=context.token.user,
            config=context.config,
            pool_seeds=context.pool_seeds,
            unknown_arguments=binding.unknown_arguments,
            context=dispatch_context,
            exception_map=context.exception_map,
        )
    except _DENIALS as exc:
        return resource_refusal(
            exc,
            KernelRefusalScene(
                arguments=arguments, config=context.config, conventions=context.conventions
            ),
        )


def admit_kernel_read(
    binding: KernelResourceBinding, variables: dict[str, str], context: MCPCallContext
) -> KernelAdmission | JsonRpcError:
    """``admit_kernel_call`` for a ``resources/read``, a denial as its answer."""
    request = carry_token(context.http_request, context.token)
    dispatched = call_context(request, binding.name)
    arguments: dict[str, Any]
    try:
        arguments = coerce_flat(binding.spec.parameters(call_context=dispatched), variables)
    except InvalidArguments:
        arguments = dict(variables)
    try:
        return admit_kernel_call(
            binding,
            arguments,
            user=context.token.user,
            config=context.config,
            pool_seeds=context.pool_seeds,
            unknown_arguments=UnknownArguments.REJECT,
            context=dispatched,
        )
    except _DENIALS as exc:
        return resource_refusal(
            exc,
            KernelRefusalScene(
                arguments=variables, config=context.config, conventions=context.conventions
            ),
        )


def page_request(
    binding: KernelToolBinding, arguments: dict[str, Any], config: MCPConfig
) -> tuple[dict[str, Any], KernelPageRequest | None]:
    """Split a paginated ``LIST`` tool's ``page`` / ``limit`` off its arguments."""
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
    """``result`` presented for an agent, as one page when the tool paginates."""
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
    """Whether the kernel describes what ``binding`` presents as a list or nothing."""
    schema = spec_output_schema(binding.spec)
    return schema is not None and schema.get("type") == ["array", "null"]


def bound_unpaginated_rows(
    binding: KernelToolBinding,
    result: DispatchResult,
    page: KernelPageRequest | None,
    config: MCPConfig,
    *,
    exception_map: Mapping[type[Exception], ExceptionHandler],
    span: Any = None,
) -> DispatchResult | ToolResult:
    """``result`` with an unpaginated ``LIST`` tool's rows taken up to the row ceiling."""
    max_rows = resolve_bound(binding.max_page_size, config.max_page_size)
    if page is not None or binding.kind is not SelectorKind.LIST or max_rows is None:
        return result
    rows: Any = result.value
    try:
        window: Any = (
            rows[: max_rows + 1] if is_queryset(rows) else list(islice(rows, max_rows + 1))
        )
        over_the_ceiling = len(window) > max_rows
    except Exception as exc:
        handler = exception_handler_for(exc, exception_map)
        if handler is None:
            raise
        return answer_mapped_exception(handler, exc, config=config, span=span)
    if over_the_ceiling:
        return _over_row_ceiling(binding, max_rows)
    return replace(result, value=window)


def _over_row_ceiling(binding: KernelToolBinding, max_rows: int) -> ToolResult:
    """The refusal of an unpaginated result carrying more rows than the ceiling."""
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
    """A ``not_found`` dispatch result as the tool's ``not_found`` error result."""
    if binding.kind is SelectorKind.LIST:
        return build_error_tool_result(f"{binding.name}: not found", error_type="not_found")
    return build_error_tool_result(
        f"{binding.name}: no matching instance found", error_type="not_found"
    )


def tool_result(binding: KernelToolBinding, payload: Any, *, config: MCPConfig) -> ToolResult:
    """The successful ``tools/call`` result for ``payload``; a file is read under the ceiling."""
    if isinstance(payload, FileResult):
        if binding.content_kind not in FILE_CONTENT_KINDS:
            payload.close()
            return build_error_tool_result(
                f"Tool {binding.name!r} produced a FileResult, and its spec presents no file. "
                "Present it through FilePresenter(content_type), which declares the file.",
                error_type="output_encoding",
            )
        label = f"Tool {binding.name!r}"
        read = _read_file(payload, resolve_bound(binding.max_result_bytes, config.max_result_bytes))
        if isinstance(read, FileTooLarge):
            return build_error_tool_result(
                _over_file_ceiling(label, read), error_type="result_too_large"
            )
        payload = read
    elif payload is None and binding.content_kind in FILE_CONTENT_KINDS:
        return build_tool_result(None, include_structured_content=False)
    _emit_output_schema, emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
        default_output_schema=config.include_output_schema,
        default_structured_content=config.include_structured_content,
    )
    return build_tool_result(
        payload,
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
    """The ``resources/read`` answer for a kernel resource's dispatch ``result``."""
    if result.kind == "not_found":
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Resource not found: {uri!r}",
            data={"uri": uri},
        )
    media_type: str | None = None
    if isinstance(payload, FileResult):
        media_type = payload.content_type
        payload = _read_file(payload, context.config.max_result_bytes)
        if isinstance(payload, FileTooLarge):
            return JsonRpcError(
                JsonRpcErrorCode.SERVER_ERROR, _over_file_ceiling(f"Resource {uri!r}", payload)
            )
        payload = payload.content
    contents = encode_resource_contents(payload, binding=binding, uri=uri, producer=_PRESENTED_BY)
    if isinstance(contents, JsonRpcError):
        return contents
    if media_type is not None:
        contents = replace(contents, mime_type=media_type)
    return resource_read_answer(contents, binding, uri, context)


def _read_file(file: FileResult, max_bytes: int | None) -> FileResult | FileTooLarge:
    """``file`` read into memory under ``max_bytes``, or the refusal; either way it is closed."""
    try:
        return FileResult(
            file.read(max_bytes), filename=file.filename, content_type=file.content_type
        )
    except FileTooLarge as exc:
        return exc


def _over_file_ceiling(label: str, exc: FileTooLarge) -> str:
    """The ceiling refusal for a file, worded as ``enforce_result_bytes`` words a payload's."""
    logger.warning(
        "Result bound exceeded: %s produced a file over a %d byte ceiling", label, exc.max_bytes
    )
    return (
        f"{label} produced a file over this server's {exc.max_bytes} byte ceiling. Narrow "
        "the request and call again. The file was not truncated: a partial file would "
        "look complete."
    )


def resource_refusal(exc: DispatchError | ServiceError, scene: KernelRefusalScene) -> JsonRpcError:
    """A kernel refusal on ``resources/read``, where there is no ``isError`` result."""
    if isinstance(exc, InvalidArguments):
        return invalid_arguments_error(exc.detail, scene.arguments, scene.config)
    answer = answer_kernel_refusal(exc, scene)
    if isinstance(answer, JsonRpcError):
        return answer
    raise exc


def invalid_arguments_error(
    detail: Any, arguments: Mapping[str, Any], config: MCPConfig
) -> JsonRpcError:
    """The ``-32602`` a ``resources/read`` answers for arguments it refuses."""
    return JsonRpcError(
        JsonRpcErrorCode.INVALID_PARAMS,
        "Invalid arguments",
        data=json.loads(
            json.dumps(
                validation_error_data(
                    detail, arguments, include_value=config.include_validation_value
                ),
                default=str,
            )
        ),
    )


def tool_refusal_scene(
    binding: KernelToolBinding,
    arguments: dict[str, Any],
    prior: ResolvedInput,
    context: MCPCallContext,
    otel_span: Any,
    dispatch_context: dict[str, Any],
) -> KernelRefusalScene:
    """The scene a ``tools/call`` refusal is answered in, on either transport."""
    return KernelRefusalScene(
        arguments=arguments,
        config=context.config,
        conventions=context.conventions,
        input_round=None
        if binding.is_selector
        else KernelInputRound(
            prior, context, admitted_arguments(binding, context.pool_seeds, dispatch_context)
        ),
        span=otel_span,
    )


def admitted_arguments(
    binding: KernelToolBinding, pool_seeds: PoolSeeds, dispatch_context: dict[str, Any]
) -> frozenset[str]:
    """The argument names a client may answer an ``AdditionalInputRequired`` under."""
    owned = server_owned_keys(binding.spec, pool_seeds=pool_seeds, call_context=dispatch_context)
    return frozenset(binding.spec.parameters(call_context=dispatch_context).names()) - (
        binding.server_arguments.names() | owned
    )


def _coerce_int(value: Any, *, default: int) -> int:
    """``value`` as an int, or ``default`` for anything that is not one."""
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
    "tool_refusal_scene",
    "KernelAdmission",
    "KernelPageRequest",
    "admit_kernel_call",
    "admit_kernel_read",
    "admit_kernel_tool_call",
    "admits_without_arguments",
    "bound_unpaginated_rows",
    "call_context",
    "carry_token",
    "copy_request",
    "invalid_arguments_error",
    "not_found_result",
    "on_the_wire",
    "page_request",
    "principal_of",
    "present_tool_result",
    "resource_read_result",
    "resource_refusal",
    "seat",
    "tool_call_context",
    "tool_result",
    "unguarded_grant",
]
