"""Helpers the kernel dispatch path shares across its sync, async and in-process callers.

Kept apart from ``handlers/utils.py``, which holds the transport's own checks
and result shapes shared with prompts, views and completions, so a helper here
is one only a call reaching ``dispatch`` needs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import islice
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_PAGE_SIZE,
    DispatchError,
    DispatchResult,
    Grant,
    InvalidArguments,
    NotPermitted,
    OutputPage,
    PoolSeeds,
    PrincipalUnavailable,
    SelectorKind,
    ServiceError,
    ServiceSpec,
    UnknownArguments,
    authorize,
    authorize_scope,
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
from django_spec_mcp.handlers.types.context import MCPCallContext
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
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode

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
    """A kernel call its spec's class-level checks admitted, as ``dispatch`` is to run it.

    ``principal`` is the very object the grant was minted for, since a grant
    covers its principal by identity and ``principal_of`` builds a fresh
    ``AnonymousUser`` on each call
    (``test_an_anonymous_in_process_caller_reaches_a_kernel_tool``).
    ``call_context`` is the dict the checks were handed. ``grant`` is the one
    dispatch runs under:
    the checks' own, so a call whose arguments pass is not asked again
    (``test_a_check_is_asked_once_per_call``), or what dispatch would have
    been handed without this step, for a call whose shape check answers first.
    """

    principal: Any
    grant: Grant | None
    call_context: dict[str, Any]


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
    """Make ``request`` the caller's: the token on it, and its principal as the user.

    Hangs the resolved ``TokenInfo`` on ``request`` as ``mcp_token``, and sets
    ``request.user`` (and ``request.auser``, its async reader) to the token's
    principal, ``principal_of(token.user)``: the user the call dispatches as,
    or an ``AnonymousUser`` when the token names none. The request a kernel
    spec's ``call_context``, a prompt's ``render``, a completer, a pool seed on
    a resource read and a view are handed comes from here, or, for sync
    ``call_tool``'s kernel tool, which has no token, from ``seat`` on a copy.
    A spec reading the caller's token (its scopes, its
    audience) reads it off this request, which is where a permission check
    over HTTP finds it.

    **The user is the token's, never the endpoint's.** Over the wire the
    project's middleware resolved ``request.user`` from whatever the request
    carried, a session cookie included, and that can name someone other than
    the bearer token's principal, so code authorizing on ``request.user``
    judged the wrong caller
    (``test_every_path_sees_the_tokens_principal_not_the_sessions``); with no
    principal it is anonymous, not the session's user
    (``test_a_caller_the_token_names_no_one_is_anonymous_not_the_sessions_user``).

    The wire's inbound request is written to rather than copied, because a spec
    reading ``call_context["request"]`` is handed the very request the
    transport received
    (``test_a_resource_permission_reads_the_request_the_read_arrived_on``).
    Every call on one request carries the one token the transport resolved for
    it, a batch's and a stream's included, so a second write sets what the
    first did. The transport makes that first write the moment the request
    authenticates (``carry_principal``), so anything judged before any
    handler runs sees the same principal. An in-process call's is a copy
    already, made where its context is (``copy_request``).
    """
    request.mcp_token = token
    return seat(request, principal_of(token.user))


def seat(request: Any, principal: Any) -> Any:
    """Set ``principal`` as ``request``'s user, for its sync and async readers both.

    ``AuthenticationMiddleware`` sets ``request.auser`` beside ``request.user``,
    and a request keeping the middleware's would answer an async reader with the
    session's user while the sync one names the caller, so both are written.
    """

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
    """The base of a copy ``copy_request`` makes, sharing its shared names with the handed request.

    Every read, write and delete of a name in ``_shared_names`` lands on the
    handed request, held as ``_shares_with``; every other name is the copy's
    own. A value written to a shared name that is itself a copy is written as
    the request it copies, so the handed request never holds a copy, and with
    it the call's user: DRF opens its stream over the request it wraps, which
    for a copy is the wrapped request's copy.
    """

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
        """Initialise the handed request's upload handlers, bound to it rather than to the copy.

        Django's ``upload_handlers`` getter fills ``_upload_handlers``, which is
        shared, with handlers ``load_handler`` binds to ``self``. Bound to the
        copy, they would stay in the caller's request, carrying the call's user
        into every parse it runs afterwards
        (``test_upload_handlers_a_spec_reads_are_bound_to_the_handed_request``).
        So a multipart parse a spec triggers runs handlers bound to the handed
        request, and a handler reading ``self.request.user`` reads the
        caller's, as a parse through a DRF ``Request`` does.
        """
        object.__getattribute__(self, "_shares_with")._initialize_handlers()


def _holder(clone: _SharesBodyState, name: str) -> Any:
    """What ``name`` is read on for ``clone``: the handed request, or the copy itself."""
    if name in type(clone)._shared_names:
        return object.__getattribute__(clone, "_shares_with")
    return super(_SharesBodyState, clone)


def _sharing_class(
    cls: type, shared: frozenset[str], classes: dict[tuple[type, frozenset[str]], type]
) -> type:
    """A subclass of ``cls`` whose instances share ``shared`` with the request they copy.

    It takes ``cls``'s name, qualified name and module, so a copy's ``repr``
    and any message naming its type read as the handed request's.

    Made once per server and kept in ``classes``, the server's own, rather
    than per copy or in a module-level cache, which the package does not keep.
    A class is cyclic, so one made per copy outlives it until the collector
    runs, and back-to-back calls pile them up; making one also runs the
    request class's ``__init_subclass__``. Two threads making the first one at
    once each make one, and ``setdefault`` keeps the first.
    """
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
    """A copy of ``request`` for one in-process call to write its user to.

    A request an in-process caller hands ``call_tool`` / ``acall_tool`` is the
    caller's own, often the very request a view is serving, so the call's user
    is never written to it: writing it there would leave the view serving as
    someone else after the call
    (``test_an_in_process_call_leaves_the_handed_request_as_it_was``), and two
    concurrent calls sharing it would each see the other's
    (``test_concurrent_in_process_calls_on_one_request_keep_their_own_users``,
    ``test_concurrent_calls_on_one_asgi_request_keep_their_own_users``).

    The copy's body and parse state are the handed request's, read and
    written there, so a spec reading or parsing it does so for the caller
    exactly as if the request itself had been handed over: a body a spec
    reads is cached for the caller, a stream it drains is drained for the
    caller, and a body it does not read is left unread. The names shared are
    ``_REQUEST_SHARES`` above, each held by a test:

    - ``_body``, ``_stream``:
      ``test_a_spec_reading_the_body_reads_it_for_the_caller``;
    - ``_read_started``:
      ``test_a_spec_draining_the_stream_drains_it_for_the_caller``;
    - ``_post``, ``_files``:
      ``test_a_form_a_spec_parsed_is_parsed_for_the_caller``;
    - ``_encoding``, ``GET``: ``test_an_encoding_a_spec_sets_is_the_callers``;
    - ``_upload_handlers``:
      ``test_upload_handlers_a_spec_sets_are_the_callers``, the handlers
      Django initialises being bound to the handed request
      (``_SharesBodyState._initialize_handlers``);
    - ``_multipart_parser_class``, Django 6.1's:
      ``test_a_multipart_parser_a_spec_sets_is_the_callers``.

    They are shared by name, through the copy's class, rather than by
    overriding ``body`` or ``POST``, because Django's own methods read and
    write them on ``self``. Everything else is a shallow copy of the
    ``__dict__``, taken onto an instance made with ``object.__new__``, which
    skips ``__init__`` and the ``__reduce_ex__`` route ``copy.copy`` takes.
    So every other attribute holds, when the copy is made, the very object
    the handed request's does, and the line falls between rebinding and
    mutating: an attribute a spec sets or deletes on the copy, the user the
    call seats among them, is rebound on the copy alone
    (``test_deleting_an_attribute_on_the_copy_leaves_the_caller_its_own``),
    and one the caller sets after the copy is made is not seen by it
    (``test_an_attribute_the_caller_sets_after_the_copy_is_not_the_copys``);
    but an object a spec mutates through one, ``META``, the session, the
    message storage, the cookies once read, is the caller's own.

    The copy is an instance of a subclass of the handed request's class,
    under its name, qualified name and module, made once per server and kept
    in ``classes`` (``test_a_copy_reads_as_the_request_it_copies``). So
    ``isinstance(copy, WSGIRequest)`` holds where ``type(copy) is
    WSGIRequest`` does not, and a copy does not pickle: pickling looks its
    class up by name and finds the handed request's
    (``test_a_copy_does_not_pickle``).

    A wrapped request, DRF's ``Request``, keeps its ``HttpRequest`` as
    ``_request`` and its ``user`` setter writes through to it, so the wrapped
    request is copied too (``test_a_wrapped_request_keeps_its_user``), and the
    wrapper shares its parse cache, ``_WRAPPER_SHARES``, with the handed
    wrapper (``test_a_wrapped_requests_parse_is_the_callers``). A copy handed
    on is copied from the request it copies, so a spec passing its request to
    a further call shares the one body
    (``test_a_spec_handing_its_request_on_shares_the_callers_body``).
    """
    if isinstance(request, _SharesBodyState):
        cls: type = type(request)
        shared = type(request)._shared_names
        source = object.__getattribute__(request, "_shares_with")
    else:
        shared = _WRAPPER_SHARES if "_request" in request.__dict__ else _REQUEST_SHARES
        cls = _sharing_class(type(request), shared, classes)
        source = request
    clone: Any = object.__new__(cls)
    # Taken whole before it is filtered: another thread's call may be writing
    # the handed request's shared names while this one copies it, and one
    # C-level copy cannot see a write mid-way where iterating the live
    # ``__dict__`` raises for it
    # (``test_a_request_written_to_while_it_is_copied_is_copied_whole``).
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
    """Whether ``binding``'s spec lets ``principal`` past its checks, asked with no arguments.

    The question a listing filtered by permission asks of a kernel spec
    (``is_binding_listable``), the one a completion is asked, since it has no
    arguments (``handle_completion_complete``), and the one a call whose
    arguments fail the shape check is asked (``admit_kernel_call``), so a
    caller is refused
    before it learns which argument it left out exactly when that listing
    would hide the tool from it
    (``test_a_call_failing_its_shape_is_refused_first_exactly_when_the_listing_hides_it``).
    ``always_listed`` lists a tool without changing this answer
    (``test_an_always_listed_tool_still_refuses_a_call_failing_its_shape``).
    Asked with what a call hands dispatch: the same principal, the grant only
    the registrar's mark earns, the server's pool seeds, and a call context
    whose request carries the token. No arguments: a listing has none, a call
    failing its shape has no checked ones, and ``None`` is the kernel's word
    for that.

    Two questions, the two a call asks before it reads anything. ``authorize``
    is the class-level one. ``authorize_scope`` of ``None`` is the scope-level
    one dispatch asks of a create, a service spec with neither an instance nor
    a collection selector, which resolves no row to ask about; any other spec's
    scope question is of a row or a set that only the call's arguments name, and
    stays unasked.

    Refused when either is (``NotPermitted``), when the principal is
    deactivated (``PrincipalUnavailable``, which ``authorize`` raises when
    handed pool seeds as dispatch does, ahead of any grant, so on an unguarded
    spec too), and when a check cannot answer without the call's arguments and
    raises on their absence. A configuration error (``ImproperlyConfigured``)
    is raised, as the call raises it.
    """
    grant = unguarded_grant(binding, principal, config)
    if grant is None and binding.spec.permissions is None:
        # A spec declaring no checks with no grant is refused at every call, so
        # it is admitted to no one; asking ``authorize`` would raise its
        # configuration error and fail the whole listing instead. Both halves
        # are held: without the grant's, a marked binding granted by a server
        # with the setting off is hidden
        # (test_a_registered_unguarded_tool_is_listed_where_its_call_is_granted);
        # without the permissions', every guarded spec is
        # (test_a_tool_whose_checks_admit_the_caller_is_listed).
        return False
    # Whether dispatch asks this spec the scope question of nothing, as it does
    # a create (``conclude_service`` in the kernel). One arc to coverage, so each
    # conjunct is held by its own test: the first by
    # test_a_selector_tool_is_not_asked_the_scope_of_nothing, the second by
    # test_a_service_with_a_target_is_not_asked_the_scope_of_nothing.
    asks_scope_of_nothing = (
        isinstance(binding.spec, ServiceSpec) and binding.spec.target_selector_spec() is None
    )
    try:
        granted = authorize(
            binding.spec,
            principal,
            grant=grant,
            arguments=None,
            pool_seeds=pool_seeds,
            call_context=call_context,
        )
        if asks_scope_of_nothing:
            authorize_scope(
                binding.spec,
                principal,
                None,
                grant=granted,
                arguments=None,
                pool_seeds=pool_seeds,
                call_context=call_context,
            )
    except ImproperlyConfigured:
        # A configuration error the call raises as well, so the listing and
        # the call fail with it rather than refusing for it: without this
        # clause the arm below would swallow it
        # (test_a_check_left_awaiting_its_answer_still_fails_the_listing,
        # test_a_misconfigured_check_fails_a_call_failing_its_shape_as_it_fails_the_listing).
        raise
    except Exception:
        # Refused, and only this binding. Every way the kernel has of saying no
        # is an exception: a refusal (``NotPermitted``), a deactivated caller
        # (``PrincipalUnavailable``), and a check that cannot decide without
        # the call's arguments, which it is not handed, so it raises on their
        # absence (a ``TypeError`` or ``KeyError`` on ``None``). The last is
        # the docs' "conservatively deny at list time"; failing the listing
        # for it would hide every other binding too. Held by
        # test_a_tool_is_hidden_from_the_caller_its_spec_s_checks_refuse,
        # test_a_deactivated_caller_is_listed_nothing and
        # test_a_check_reading_arguments_hides_its_tool_and_no_other.
        return False
    return True


def admit_kernel_call(
    binding: KernelToolBinding | KernelResourceBinding,
    arguments: dict[str, Any],
    *,
    user: Any,
    request: Any,
    config: MCPConfig,
    pool_seeds: PoolSeeds,
    unknown_arguments: UnknownArguments,
) -> KernelAdmission:
    """Ask a kernel spec's class-level checks about a call before the transport acts on it.

    The kernel's ``dispatch`` checks a call's arguments before it asks the
    spec's checks, and a kernel binding's own ``permissions`` hold only its
    ``scopes=`` gate. So every route that charges a rate limit, creates a task
    or answers a missing argument asks the spec first, here: a caller the tool is not for is refused before it is charged
    (``test_a_caller_the_spec_refuses_is_not_charged``) and before it learns
    which arguments the tool takes
    (``test_a_caller_the_spec_denies_is_refused_before_the_argument_is_checked``).

    ``arguments`` are the ones dispatch is to receive. The checks are asked on
    them as dispatch asks them, through ``check_call``, the kernel's own
    prelude, rather than through ``authorize`` on the arguments as sent:
    ``authorize`` hands a check declaring ``arguments`` the shape check's
    output, which is not what was sent. ``"8"`` for an ``integer`` reaches the
    run as ``8``, so a check shown the string compared ``"8" != 8``, admitted,
    and the run went ahead on the row it refuses
    (``test_a_check_reads_the_arguments_as_the_shape_check_reads_them``).
    Nor ``coerce_flat``, which reads flat strings and is not the shape check.

    A call whose arguments fail the shape check has no checked form, and a
    check is never shown the arguments as sent, a third input no check is
    written for. It is judged instead as a listing filtered by permission
    judges the spec, on no arguments (``admits_without_arguments``). Whatever
    that says, the call cannot run, so it only chooses which refusal is told:
    ``-32006`` before the charge, exactly when that listing would hide the
    tool from this caller
    (``test_a_call_failing_its_shape_is_refused_first_exactly_when_the_listing_hides_it``),
    or, when it admits, the shape check's, from dispatch after the charge
    (``test_an_admitted_caller_missing_an_argument_is_charged_and_told_which``).
    dispatch is then handed what it would have been handed without this step.

    A ``many`` call whose top level passes and whose rows do not is no
    failed shape: ``check_call`` asked the checks on the checked top level
    before it raised the row, and marks that refusal ``after_authorize``, so
    their answer stands, a refusal as ``-32006`` and an admission leaving the
    row to dispatch, never judged again on no arguments
    (``test_a_many_call_with_a_refused_row_is_answered_on_its_checked_top_level``).
    The mark is the kernel's, not a reading of the refusal's tree: a check
    raising a tree shaped like a refused row keeps no admission
    (``test_a_check_raising_a_refusal_shaped_like_a_refused_row_keeps_no_admission``),
    and neither a spec that is not ``many`` failing an array at an index
    (``test_an_array_argument_failing_at_an_index_is_no_refused_row``) nor a
    ``many`` list that is no list
    (``test_a_many_list_that_is_no_list_is_a_failed_shape``) reads as one.

    So the checks are asked once on a call whose arguments pass, and once on
    one whose arguments fail, by that judgment, since dispatch refuses the
    shape before it asks them. An admitted ``many`` call with a refused row is
    asked twice: ``check_call`` hands back no grant when it raises the row, so
    dispatch asks again before it reports the row beside the Validator's
    refusals of the other rows, which ``check_call`` does not run
    (``test_a_many_call_with_a_refused_row_is_asked_by_its_prelude_and_by_dispatch``).
    A task's worker asks them again when it runs the call.

    Raises a denial, ``NotPermitted`` or ``PrincipalUnavailable``, for the
    caller to answer as ``KERNEL_REFUSALS`` does. Any other refusal is left to
    dispatch, which raises it again in its own order, where the call's arms answer it.
    What is no refusal, a configuration error
    (``test_a_handbuilt_unguarded_kernel_tool_is_never_called``) or a check
    that raises, is raised here, before the charge, as dispatch would have
    raised it after; on a streamed call that is still before the stream
    commits its status
    (``test_a_permission_preflight_that_raises_before_a_stream_is_a_json_rpc_error``).
    """
    principal: Any = principal_of(user)
    context = call_context(request, binding.name)
    unchecked = unguarded_grant(binding, principal, config)
    keywords: dict[str, Any] = {"pool_seeds": pool_seeds, "call_context": context}
    try:
        _checked, grant = check_call(
            binding.spec,
            principal=principal,
            arguments=arguments,
            grant=unchecked,
            unknown_arguments=unknown_arguments,
            **keywords,
        )
    except _DENIALS:
        raise
    except InvalidArguments as refusal:
        if not refusal.after_authorize and not admits_without_arguments(
            binding, principal, config=config, pool_seeds=pool_seeds, call_context=context
        ):
            raise NotPermitted() from None
        grant = unchecked
    except (DispatchError, ServiceError):
        # A refusal that is no denial, such as a ``ServiceError`` a check or a
        # seed's resolver raises: dispatch raises it again and the call's own
        # arms answer it, after the charge, and on a stream inside
        # it (``test_a_service_error_a_check_raises_streams_the_plain_calls_answer``).
        # One arc to coverage, each kind held by its case of
        # ``test_a_check_raising_a_refusal_that_is_no_denial_is_answered_as_before``.
        grant = unchecked
    return KernelAdmission(principal=principal, grant=grant, call_context=context)


def admit_kernel_tool_call(
    binding: KernelToolBinding, arguments: dict[str, Any], context: MCPCallContext
) -> KernelAdmission | JsonRpcError:
    """``admit_kernel_call`` for a ``tools/call`` over the wire, a denial as its answer.

    ``arguments`` carry the server's arguments and any answers merged in; the
    page a paginated tool takes is split off, as ``run_kernel_tool`` splits it
    before dispatch, and the shape is checked under the tool's own
    ``unknown_arguments``, as dispatch checks it. Otherwise an admitted call
    would fail its shape here alone, be judged on no arguments, and be asked
    again by dispatch (``test_a_paged_call_is_asked_once``,
    ``test_a_stray_argument_a_tool_ignores_is_asked_once``).
    """
    dispatched, _page = page_request(binding, arguments, context.config)
    try:
        return admit_kernel_call(
            binding,
            dispatched,
            user=context.token.user,
            request=carry_token(context.http_request, context.token),
            config=context.config,
            pool_seeds=context.pool_seeds,
            unknown_arguments=binding.unknown_arguments,
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
    """``admit_kernel_call`` for a ``resources/read``, a denial as its answer.

    Asked on the URI's variables read as the spec's types, the arguments
    ``read_kernel_resource`` dispatches with: ``true`` reaches the read as
    ``True``, so a check refusing ``True`` refuses before the charge
    (``test_a_reader_is_judged_on_the_variables_the_read_dispatches``). A
    variable ``coerce_flat`` cannot read has no checked form, so the call is
    judged on no arguments, as ``admit_kernel_call`` judges any failed shape: a
    reader that judgment refuses is ``-32006`` before the charge
    (``test_a_reader_refused_on_a_malformed_variable_is_told_so``), and one it
    admits is charged and then told which variable is wrong, ``-32602`` with the
    kernel's detail (``resource_refusal``,
    ``test_an_admitted_reader_of_a_malformed_variable_is_charged_and_told_which``).

    ``REJECT`` is the read's own policy, the one ``read_kernel_resource``
    dispatches under, and it answers as ``IGNORE`` would, since every
    variable a URI template names is a read the spec declares: registration
    refuses a template naming another
    (``test_a_template_variable_no_read_declares_is_refused_at_registration``).
    """
    arguments: dict[str, Any]
    try:
        arguments = coerce_flat(binding.spec.parameters(), variables)
    except InvalidArguments:
        arguments = dict(variables)
    try:
        return admit_kernel_call(
            binding,
            arguments,
            user=context.token.user,
            request=carry_token(context.http_request, context.token),
            config=context.config,
            pool_seeds=context.pool_seeds,
            unknown_arguments=UnknownArguments.REJECT,
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
    """Split a paginated ``LIST`` tool's ``page`` / ``limit`` off its arguments.

    Every other binding's arguments are returned whole, with no page. The clamps
    and the envelope arithmetic are ``paginate_output``'s; this only reads the
    two untyped values as ints, falling back to the first page and the kernel's
    default size.
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
    rather than the ``{}`` a ``None`` becomes
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
    # A spec with no presenter has no output schema (``test_no_presenter_yields_no_schema``),
    # and what it returns as nothing is served as the object ``{}``, not a list
    # (``test_a_service_returning_none_still_serves_an_object``,
    # ``test_an_allow_none_retrieve_returns_an_empty_result_not_an_error``: both
    # reach this with no schema, which the ``is not None`` conjunct answers).
    # The type keeps a single row that presents nothing serving ``{}``
    # (``test_a_result_that_presents_nothing_conforms_to_its_schema``).
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
    (``test_the_ceiling_bounds_the_fetch_and_not_just_the_payload``). That is
    the point of doing this before presenting rather than leaning on the byte
    ceiling, which measures a payload already fetched and presented in full.
    Under the ceiling the window itself is what the presenter then iterates: a
    QuerySet's slice has its result cache filled, so the table is read once
    (``test_a_queryset_under_the_ceiling_is_read_once``), and a generator the
    window read has spent is served from the window, not as empty
    (``test_a_generator_under_the_ceiling_is_served_whole``).
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
    """A ``not_found`` dispatch result as the tool's ``not_found`` error result.

    A ``LIST`` has no instance to miss, so its wording is the selector tool's
    ``not found``; a ``RETRIEVE`` and a service tool's instance selector both
    miss an instance.
    """
    if binding.kind is SelectorKind.LIST:
        return build_error_tool_result(f"{binding.name}: not found", error_type="not_found")
    return build_error_tool_result(
        f"{binding.name}: no matching instance found", error_type="not_found"
    )


def tool_result(binding: KernelToolBinding, payload: Any, *, config: MCPConfig) -> ToolResult:
    """The successful ``tools/call`` result for ``payload``."""
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
    """The ``resources/read`` answer for a kernel resource's dispatch ``result``.

    A ``not_found`` is the resource-not-found error the protocol version names,
    with the URI in ``data``, as an unknown URI is answered. Otherwise the
    presented ``payload`` is the one ``contents`` block, encoded per the
    binding's ``encoding`` by ``encode_resource_contents``, the step a view's
    read shares: pretty-printed JSON by default, the presented
    ``str`` verbatim for ``TEXT``, presented bytes base64-encoded into ``blob``
    for ``BLOB``. The kernel's ``present`` hands a presenter's ``str`` or bytes
    through as returned, and a spec with no presenter its selector's value. A
    presented value of the wrong type for the encoding is the
    ``INTERNAL_ERROR`` that function answers, naming the presenter. Every
    encoding carries the binding's ``_meta``, the cache hints and the server's
    result ceiling, as a view's read does.
    """
    if result.kind == "not_found":
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Resource not found: {uri!r}",
            data={"uri": uri},
        )
    contents = encode_resource_contents(payload, binding=binding, uri=uri, producer=_PRESENTED_BY)
    if isinstance(contents, JsonRpcError):
        return contents
    return resource_read_answer(contents, binding, uri, context)


def resource_refusal(exc: DispatchError | ServiceError, scene: KernelRefusalScene) -> JsonRpcError:
    """A kernel refusal on ``resources/read``, where there is no ``isError`` result.

    ``InvalidArguments`` is ``-32602`` with the kernel's ``detail`` tree in
    ``data`` (``test_an_admitted_reader_of_a_malformed_variable_is_charged_and_told_which``
    and its HTTP twin), the reader having been admitted and charged, as an
    admitted tool call is told which argument is wrong after its charge. The
    message is fixed and carries no value; the detail names the variable and the
    kernel's own problem with it, and ``include_validation_value`` adds what was
    sent, as it does for a tool. Only a read reaches this arm: the prelude and a
    completion hand this function a denial.

    Otherwise the map's answer when it is a JSON-RPC error, which is a denied or
    deactivated principal's ``-32006``. Any other refusal is raised on, to the
    transport's internal error, as a view's read answers a selector that
    raises.
    """
    if isinstance(exc, InvalidArguments):
        return invalid_arguments_error(exc.detail, scene.arguments, scene.config)
    answer = answer_kernel_refusal(exc, scene)
    if isinstance(answer, JsonRpcError):
        return answer
    raise exc


def invalid_arguments_error(
    detail: Any, arguments: Mapping[str, Any], config: MCPConfig
) -> JsonRpcError:
    """The ``-32602`` a ``resources/read`` answers for arguments it refuses.

    A fixed message carrying no value, with ``detail`` in ``data`` naming each
    variable and its problem, and what was sent beside it when
    ``include_validation_value`` says so: ``resource_refusal``'s answer to the
    kernel's ``InvalidArguments``, and ``resource_presentation_refusal``'s to a
    presenter refusing a shape the URI carried.
    """
    # Written as JSON writes it, so the integer row indices of a many call's
    # tree are strings, as in a tool result; the kernel's order is kept.
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
) -> KernelRefusalScene:
    """The scene a ``tools/call`` refusal is answered in, on either transport:
    the call's arguments and wording, its span, and the round a request for
    input continues in."""
    return KernelRefusalScene(
        arguments=arguments,
        config=context.config,
        conventions=context.conventions,
        # A selector asks for nothing: a request for input from one is a
        # service error, not a round trip
        # (``test_a_kernel_selector_ignores_a_round_trip``).
        input_round=None
        if binding.is_selector
        else KernelInputRound(prior, context, admitted_arguments(binding, context.pool_seeds)),
        span=otel_span,
    )


def admitted_arguments(binding: KernelToolBinding, pool_seeds: PoolSeeds) -> frozenset[str]:
    """The argument names a client may answer an ``AdditionalInputRequired`` under.

    The spec's parameters less every name the server fills, since an answer comes
    back as an argument the closed set refuses by name: the binding's
    ``server_arguments`` (a parameter the server answers per mount) and the
    kernel's ``server_owned_keys`` (what a provider or a pool seed always fills),
    the two lists ``kernel_tool_input_schema`` withholds from the advertised
    schema. The set is closed under every ``unknown_arguments`` a kernel tool can
    register with: ``REJECT`` refuses an answer under any other name and
    ``IGNORE`` drops it, so the service would ask again (``PASSTHROUGH`` is
    refused at registration).

    Each subtraction is held by a test that fails without it:
    ``test_a_question_whose_answer_the_spec_refuses_is_an_internal_error`` (the
    parameters), ``test_a_field_the_server_fills_is_not_admitted`` (the server
    arguments) and ``test_a_key_a_provider_fills_is_not_admitted`` (the owned keys).
    """
    return frozenset(binding.spec.parameters().names()) - (
        binding.server_arguments.names() | server_owned_keys(binding.spec, pool_seeds=pool_seeds)
    )


def _coerce_int(value: Any, *, default: int) -> int:
    """``value`` as an int, or ``default`` for anything that is not one.

    Answering a malformed argument is the transport's policy, so it stays here
    rather than in the kernel's ``paginate_output``: a bool is refused, since
    ``True`` is an ``int``, and a numeric string is read.
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
    "tool_result",
    "unguarded_grant",
]
