"""``completion/complete`` for prompts and for kernel resource templates.

The prompt half asks what the original asked, since a prompt is not a spec.
The resource half registers kernel ``RETRIEVE`` specs through
``register_resource(spec, uri_template=..., name=...)``, whose ``reads`` are the
template's variables. A completion is a read of what it completes, so a kernel
resource's spec permissions answer it as they answer ``resources/read``: the
class-level checks, asked through the kernel's ``authorize`` with no arguments,
since a completion names a template rather than a URI, and before the rate
limit, as the read asks them. A check reading ``arguments`` is handed ``None``
there and refuses, whether it guards against ``None`` or raises on it, which is
the kernel form of the original's route-scoped template completing for nobody;
a ``ServiceError`` or a configuration error it raises fails the completion as
it fails the read.

Not carried: that a ``DRFPermissionAdapter`` on a prompt sees ``{}`` as
``request.data`` and ``None`` as ``view.action`` at completion. A DRF class has
no kernel counterpart, and an ``MCPPermission`` is handed the request and token
alone. The original is kept beside this file as ``test_completion_complete_drfs.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceNotFound,
    Unrestricted,
)

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.permissions.types.mcp_permission import MCPPermission
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_initialize import handle_initialize
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import RefusingRateLimit

LANGUAGES = ["python", "pytorch", "pyside", "ruby"]


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer, scopes: tuple[str, ...] = ()) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None, scopes=scopes),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        config=server.config,
    )


def _invoice(*checks: PermissionCheck) -> SelectorSpec:
    """A kernel ``RETRIEVE`` reading the ``pk`` its URI template names."""
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: None,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        permissions=list(checks) or [Unrestricted()],
    )


def _prompt_server(**kwargs: Any) -> MCPServer:
    server = _server()
    server.register_prompt(
        name="code_review",
        render=lambda language="python": f"Review some {language}",
        arguments=[PromptArgument(name="language"), PromptArgument(name="framework")],
        completions={"language": lambda value: [x for x in LANGUAGES if x.startswith(value)]},
        **kwargs,
    )
    return server


def _complete(server: MCPServer, params: dict[str, Any], **ctx: Any) -> Any:
    return handle_completion_complete(params, _ctx(server, **ctx))


# ----- ref/prompt -----


def test_prompt_argument_completion() -> None:
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
    )
    assert result == {"completion": {"values": ["python", "pytorch", "pyside"], "hasMore": False}}


def test_an_empty_value_is_valid_and_matches_everything() -> None:
    """A client asks the moment the field is focused, before anything is typed."""
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language"},
        },
    )
    assert result["completion"]["values"] == LANGUAGES


def test_context_arguments_reach_the_completer_by_name() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="language"), PromptArgument(name="framework")],
        completions={"framework": lambda language: [f"{language}-web"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "framework", "value": "fla"},
            "context": {"arguments": {"language": "python"}},
        },
    )
    assert result["completion"]["values"] == ["python-web"]


def test_a_completer_may_declare_the_whole_arguments_mapping() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda arguments, value: [f"{sorted(arguments)}:{value}"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "a", "value": "v"},
            "context": {"arguments": {"z": 1}},
        },
    )
    assert result["completion"]["values"] == ["['z']:v"]


def test_transport_seeds_win_over_a_same_named_sibling_argument() -> None:
    """A client-supplied ``value`` sibling must not shadow the real one."""
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda value: [value]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "a", "value": "real"},
            "context": {"arguments": {"value": "spoofed"}},
        },
    )
    assert result["completion"]["values"] == ["real"]


# ----- ref/resource -----


def test_resource_template_variable_completion() -> None:
    server = _server()
    server.register_resource(
        _invoice(),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["1", "2", "3"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/resource", "uri": "invoices://{pk}"},
            "argument": {"name": "pk", "value": ""},
        },
    )
    assert result["completion"]["values"] == ["1", "2", "3"]


def test_a_concrete_uri_is_not_mistaken_for_its_own_template() -> None:
    """``resolve`` would match ``invoices://7`` — the ref lookup must not."""
    server = _server()
    server.register_resource(
        _invoice(),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["1"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/resource", "uri": "invoices://7"},
            "argument": {"name": "pk", "value": ""},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32602


# ----- capping -----


def test_values_are_capped_at_the_spec_limit_with_has_more() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda: (str(i) for i in range(500))},
    )
    result = _complete(
        server, {"ref": {"type": "ref/prompt", "name": "p"}, "argument": {"name": "a"}}
    )
    assert len(result["completion"]["values"]) == 100
    assert result["completion"]["hasMore"] is True
    # ``total`` is optional and deliberately unset — see ``Completion``.
    assert "total" not in result["completion"]


def test_the_generator_is_sliced_not_drained() -> None:
    """101 items are pulled to decide ``hasMore``; the rest are never produced."""
    produced: list[int] = []

    def counting() -> Any:
        for i in range(10_000):
            produced.append(i)
            yield str(i)

    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": counting},
    )
    _complete(server, {"ref": {"type": "ref/prompt", "name": "p"}, "argument": {"name": "a"}})
    assert len(produced) == 101


def test_a_lone_string_is_one_suggestion_not_its_characters() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda: "python"},
    )
    result = _complete(
        server, {"ref": {"type": "ref/prompt", "name": "p"}, "argument": {"name": "a"}}
    )
    assert result["completion"]["values"] == ["python"]


# ----- guards -----


class _DenyAll(MCPPermission):
    def has_permission(self, request: Any, token: Any) -> bool:
        return False

    def required_scopes(self) -> tuple[str, ...]:
        return ()


def test_completion_runs_the_bindings_permissions() -> None:
    """Otherwise a gated resource still answers "which ids exist?" one key at a time."""
    result = _complete(
        _prompt_server(permissions=[_DenyAll()]),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32006


class _Refuses(PermissionCheck):
    """A kernel check refusing everyone, counting how often it is asked."""

    def __init__(self) -> None:
        self.asked = 0

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.asked += 1
        return False


class _GrantsProjectSeven(PermissionCheck):
    """Scopes by the ``pk`` a read names, recording what it was handed."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any = None) -> bool:
        self.seen.append(arguments)
        return arguments is not None and arguments.get("pk") == 7


_INVOICE_PK = {
    "ref": {"type": "ref/resource", "uri": "invoices://{pk}"},
    "argument": {"name": "pk", "value": ""},
}


def test_a_kernel_resources_completion_asks_its_spec_permissions() -> None:
    # The scope gate alone answered a kernel resource's completion, so a spec
    # refusing the caller still listed which ``pk`` values exist.
    check = _Refuses()
    server = _server()
    server.register_resource(
        _invoice(check),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["1", "2"]},
    )

    result = _complete(server, _INVOICE_PK)

    assert isinstance(result, JsonRpcError)
    assert result.code == -32006
    assert check.asked == 1


def test_a_kernel_resources_completion_is_judged_on_no_arguments() -> None:
    # Fails closed: a completion names a template, not a URI, so a check
    # scoping by a variable is handed ``None`` and refuses, whatever siblings
    # the request carries, as it would admit a read of ``invoices://7``.
    check = _GrantsProjectSeven()
    server = _server()
    server.register_resource(
        _invoice(check),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["7", "8"]},
    )

    result = _complete(server, {**_INVOICE_PK, "context": {"arguments": {"pk": "7"}}})

    assert isinstance(result, JsonRpcError)
    assert result.code == -32006
    assert check.seen == [None]


def test_a_kernel_resources_scope_gate_answers_before_its_spec() -> None:
    # The scope gate is the shell's and answers first, with the scopes the
    # token lacks; the spec's check is never asked for a caller it refuses.
    check = _Refuses()
    server = _server()
    server.register_resource(
        _invoice(check),
        name="invoice",
        uri_template="invoices://{pk}",
        scopes=("invoices:read",),
        completions={"pk": lambda: ["1"]},
    )

    result = _complete(server, _INVOICE_PK)

    assert isinstance(result, JsonRpcError)
    assert result.code == -32006
    assert result.data == {"requiredScopes": ["invoices:read"]}
    assert check.asked == 0


class _RaisesNotFound(PermissionCheck):
    """A kernel check that answers by raising a ``ServiceError``, as one may."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        raise ServiceNotFound("no such tenant")


def test_a_check_raising_a_service_error_fails_the_completion_as_the_read() -> None:
    # ``authorize`` lets a check's ``ServiceError`` through, and the read
    # raises it on to the transport's internal error; the completion does the
    # same rather than answering with a refusal the read would not give.
    server = _server()
    server.register_resource(
        _invoice(_RaisesNotFound()),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["1"]},
    )

    with pytest.raises(ServiceNotFound):
        _complete(server, _INVOICE_PK)
    with pytest.raises(ServiceNotFound):
        handle_resources_read({"uri": "invoices://1"}, _ctx(server))


class _KeyedOnPk(PermissionCheck):
    """Scopes by the ``pk`` a read names, with no guard for a call naming none."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments["pk"] == 1


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def keyed_caller(request: pytest.FixtureRequest, client: Client) -> Iterator[KernelClient]:
    """A caller of a mount whose resource is guarded by ``_KeyedOnPk``, over the wire."""
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_resource(
        _invoice(_KeyedOnPk()),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: pytest.fail("the completer ran for a refused caller")},
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=request.param)):
        caller = KernelClient(client)
        caller.initialize()
        yield caller


@pytest.mark.django_db(transaction=True)
def test_a_check_raising_on_no_arguments_refuses_the_completion_as_the_read_refuses(
    keyed_caller: KernelClient,
) -> None:
    # A check that indexes ``arguments`` raises ``TypeError`` on the ``None`` a
    # completion hands it. That was an internal error (HTTP 500, -32603) where a
    # read the check refuses answers -32006; it is the same refusal now, on the
    # same HTTP status.
    completed = keyed_caller.post("completion/complete", _INVOICE_PK)
    read = keyed_caller.post("resources/read", {"uri": "invoices://2"})

    assert completed.status_code == read.status_code
    assert completed.json()["error"]["code"] == read.json()["error"]["code"] == -32006
    assert completed.json()["error"]["message"] == "Insufficient permission"


class _AnswersWithACoroutine(PermissionCheck):
    """A sync check handing back a coroutine, which ``authorize`` refuses as misconfigured."""

    def has_permission(self, principal: Any, spec: Any) -> Any:
        async def granted() -> bool:
            return True

        return granted()


def test_a_misconfigured_check_fails_the_completion_as_the_read() -> None:
    # A configuration error is raised, as the read raises it, rather than read
    # as a refusal that would hide the mistake behind a -32006.
    server = _server()
    server.register_resource(
        _invoice(_AnswersWithACoroutine()),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: ["1"]},
    )

    with pytest.raises(ImproperlyConfigured, match="awaitable"):
        _complete(server, _INVOICE_PK)
    with pytest.raises(ImproperlyConfigured, match="awaitable"):
        handle_resources_read({"uri": "invoices://1"}, _ctx(server))


def test_a_kernel_resource_admitting_the_caller_completes() -> None:
    server = _server()
    server.register_resource(
        _invoice(),
        name="invoice",
        uri_template="invoices://{pk}",
        scopes=("invoices:read",),
        completions={"pk": lambda: ["1"]},
    )

    result = _complete(server, _INVOICE_PK, scopes=("invoices:read",))

    assert result == {"completion": {"values": ["1"], "hasMore": False}}


class _AlwaysLimited:
    def consume(self, request: Any, token: Any) -> int | None:
        return 30


def test_completion_runs_the_bindings_rate_limits() -> None:
    """The completion spec asks for this explicitly — typing is a request per key."""
    result = _complete(
        _prompt_server(rate_limits=[_AlwaysLimited()]),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32005
    assert result.data == {"retryAfter": 30}


def test_a_kernel_resources_spec_is_asked_before_its_rate_limit() -> None:
    # Asked before the charge, as the read asks it, so a caller the spec
    # refuses is told that and is charged nothing on either.
    check = _Refuses()
    limit = RefusingRateLimit()
    server = _server()
    server.register_resource(
        _invoice(check),
        name="invoice",
        uri_template="invoices://{pk}",
        rate_limits=[limit],
        completions={"pk": lambda: ["1"]},
    )

    result = _complete(server, _INVOICE_PK)

    assert isinstance(result, JsonRpcError)
    assert result.code == -32006
    assert check.asked == 1
    assert limit.consumed == 0


def test_a_kernel_resource_its_spec_admits_is_still_charged() -> None:
    # The other half: a caller the spec admits meets the rate limit after it.
    server = _server()
    server.register_resource(
        _invoice(),
        name="invoice",
        uri_template="invoices://{pk}",
        rate_limits=[_AlwaysLimited()],
        completions={"pk": lambda: ["1"]},
    )

    result = _complete(server, _INVOICE_PK)

    assert isinstance(result, JsonRpcError)
    assert result.code == -32005


def test_unknown_prompt_is_invalid_params() -> None:
    result = _complete(
        _prompt_server(),
        {"ref": {"type": "ref/prompt", "name": "nope"}, "argument": {"name": "a"}},
    )
    assert isinstance(result, JsonRpcError) and result.code == -32602


def test_unknown_resource_is_invalid_params() -> None:
    result = _complete(
        _server(),
        {"ref": {"type": "ref/resource", "uri": "nope://{x}"}, "argument": {"name": "x"}},
    )
    assert isinstance(result, JsonRpcError) and result.code == -32602


def test_an_argument_with_no_completer_lists_what_is_completable() -> None:
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "framework", "value": "fl"},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32602
    assert result.data == {"completable": ["language"]}


@pytest.mark.parametrize(
    "params",
    [
        None,
        {"argument": {"name": "a"}},
        {"ref": "not-an-object", "argument": {"name": "a"}},
        {"ref": {"type": "ref/prompt", "name": "code_review"}},
        {"ref": {"type": "ref/prompt", "name": "code_review"}, "argument": {}},
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": 7},
        },
        {"ref": {"type": "ref/prompt"}, "argument": {"name": "a"}},
        {"ref": {"type": "ref/resource"}, "argument": {"name": "a"}},
        {"ref": {"type": "ref/nonsense"}, "argument": {"name": "a"}},
    ],
)
def test_malformed_requests_are_invalid_params(params: Any) -> None:
    result = _complete(_prompt_server(), params)
    assert isinstance(result, JsonRpcError) and result.code == -32602


def test_a_non_dict_context_is_ignored_rather_than_rejected() -> None:
    """``context`` is optional; a junk value degrades to "no siblings"."""
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
            "context": {"arguments": "not-a-mapping"},
        },
    )
    assert result["completion"]["values"] == ["python", "pytorch", "pyside"]


# ----- registration and advertisement -----


def test_a_completer_for_an_unknown_prompt_argument_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=r"\['langauge'\]"):
        server.register_prompt(
            name="p",
            render=lambda: "x",
            arguments=[PromptArgument(name="language")],
            completions={"langauge": lambda: []},
        )


def test_a_completer_for_an_unknown_template_variable_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=r"\['id'\]"):
        server.register_resource(
            _invoice(),
            name="invoice",
            uri_template="invoices://{pk}",
            completions={"id": lambda: []},
        )


def test_the_capability_is_advertised_only_when_something_is_completable() -> None:
    bare = handle_initialize({}, _ctx(_server()))
    assert "completions" not in bare.capabilities.to_dict()

    with_completer = handle_initialize({}, _ctx(_prompt_server()))
    assert with_completer.capabilities.to_dict()["completions"] == {}


def test_a_resource_completer_alone_advertises_the_capability() -> None:
    server = _server()
    server.register_resource(
        _invoice(),
        name="invoice",
        uri_template="invoices://{pk}",
        completions={"pk": lambda: []},
    )
    assert handle_initialize({}, _ctx(server)).capabilities.to_dict()["completions"] == {}


@pytest.mark.django_db(transaction=True)
async def test_async_dispatch_routes_completion_through_the_executor() -> None:
    """A completer reading the ORM must not run on the event loop."""
    from django_spec_mcp.handlers.async_dispatch import adispatch

    result = await adispatch(
        "completion/complete",
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
        _ctx(_prompt_server()),
    )
    assert result["completion"]["values"] == ["python", "pytorch", "pyside"]


def test_capabilities_describe_what_the_server_can_answer() -> None:
    """One rule for all four — nothing is advertised that would return nothing."""
    bare = handle_initialize({}, _ctx(_server())).capabilities.to_dict()
    assert bare == {}

    server = _server()
    server.register_resource(
        _invoice(),
        name="invoice",
        uri_template="invoices://{pk}",
    )
    assert handle_initialize({}, _ctx(server)).capabilities.to_dict() == {"resources": {}}


def _seed_named_siblings(*, value: str, result: Any = None, queryset: Any = None) -> list[str]:
    return [f"{result}-{queryset}"]


def test_a_sibling_named_after_a_newer_kernel_seed_does_not_reach_the_completer() -> None:
    """The strip tracks the kernel's reserved seeds, which gained ``result`` and
    ``queryset``; ``arguments`` still carries them verbatim, as caller input."""
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": _seed_named_siblings},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "a", "value": "v"},
            "context": {"arguments": {"result": "r", "queryset": "q"}},
        },
    )
    assert result["completion"]["values"] == ["None-None"]
