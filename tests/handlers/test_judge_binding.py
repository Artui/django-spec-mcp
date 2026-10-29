"""A prompt and a UI resource are gated by the kernel's ``PermissionCheck``s and ``scopes=``.

Neither is a spec, so nothing dispatches them: ``judge_binding`` asks each
check ``has_permission(principal, binding)`` itself, on every path that serves
or names one - ``prompts/list``, ``prompts/get``, ``resources/list``,
``resources/read``, ``completion/complete`` and a subscription's grant - so a
check written for a spec, on the principal alone, gates a prompt unchanged.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import PermissionCheck, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_prompts_list import handle_prompts_list
from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.prompt_argument import PromptArgument
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode

ALICE = User(username="alice")
BOB = User(username="bob")


class IsUser(PermissionCheck):
    """Admits one principal, read off the first argument and nothing else."""

    def __init__(self, user: Any) -> None:
        self.user = user

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return principal is self.user


class Recording(PermissionCheck):
    """Admits everyone and records what it was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[Any, Any]] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.asked.append((principal, spec))
        return True


def _server(*, require: bool = False, settings: Any = None) -> MCPServer:
    if settings is not None:
        settings.SPEC_MCP = {**settings.SPEC_MCP, "REQUIRE_TOOL_PERMISSIONS": require}
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer, user: Any, scopes: tuple[str, ...] = ()) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=user, scopes=scopes),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=dataclasses.replace(build_mcp_config(), filter_listings_by_permissions=True),
    )


def _prompt_names(server: MCPServer, user: Any, scopes: tuple[str, ...] = ()) -> list[str]:
    listed = handle_prompts_list(None, _ctx(server, user, scopes))
    assert isinstance(listed, dict)
    return [prompt["name"] for prompt in listed["prompts"]]


def _resource_uris(server: MCPServer, user: Any, scopes: tuple[str, ...] = ()) -> list[str]:
    listed = handle_resources_list(None, _ctx(server, user, scopes))
    assert isinstance(listed, dict)
    return [resource["uri"] for resource in listed["resources"]]


def _forbidden(result: Any, *, scopes: list[str] | None = None) -> None:
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.FORBIDDEN
    assert result.message == "Insufficient permission"
    assert result.data == ({"requiredScopes": scopes} if scopes else None)


def _rendered(result: Any) -> str:
    assert isinstance(result, dict), result
    [message] = result["messages"]
    return message["content"]["text"]


# ---------- prompts ----------


def test_a_prompt_check_is_asked_of_the_principal_and_the_binding() -> None:
    server = _server()
    check = Recording()
    binding = server.register_prompt(name="p", render=lambda: "hi", permissions=[check])

    assert _rendered(handle_prompts_get({"name": "p"}, _ctx(server, ALICE))) == "hi"

    assert check.asked == [(ALICE, binding)]


def test_a_prompt_check_on_the_principal_admits_and_refuses_it() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi", permissions=[IsUser(ALICE)])

    assert _rendered(handle_prompts_get({"name": "p"}, _ctx(server, ALICE))) == "hi"
    _forbidden(handle_prompts_get({"name": "p"}, _ctx(server, BOB)))
    assert _prompt_names(server, ALICE) == ["p"]
    assert _prompt_names(server, BOB) == []


async def test_an_async_prompts_get_asks_the_same_check() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi", permissions=[IsUser(ALICE)])

    assert _rendered(await handle_prompts_get_async({"name": "p"}, _ctx(server, ALICE))) == "hi"
    _forbidden(await handle_prompts_get_async({"name": "p"}, _ctx(server, BOB)))


def test_one_check_answers_a_spec_tool_and_a_prompt_alike(settings) -> None:
    """The check a spec carries gates a prompt unchanged: one instance, one verdict."""
    settings.SPEC_MCP = {**settings.SPEC_MCP, "FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    check = IsUser(ALICE)
    server.register_tool(
        ServiceSpec(service=lambda: None, permissions=[check]),
        name="t",
        description="Do it.",
    )
    server.register_prompt(name="p", render=lambda: "hi", permissions=[check])

    for user, admitted in ((ALICE, True), (BOB, False)):
        tool_listed = [tool["name"] for tool in server.list_tools(user=user)["tools"]]
        ctx = _ctx(server, user)
        listed = "p" in _prompt_names(server, user)
        got = handle_prompts_get({"name": "p"}, ctx)
        assert (tool_listed == ["t"]) is admitted
        assert listed is admitted
        assert isinstance(got, dict) is admitted


def test_a_prompt_takes_a_scope_gate_and_counts_as_guarded(settings) -> None:
    server = _server(require=True, settings=settings)
    server.register_prompt(name="p", render=lambda: "hi", scopes="prompts:read")

    _forbidden(handle_prompts_get({"name": "p"}, _ctx(server, ALICE)), scopes=["prompts:read"])
    assert _prompt_names(server, ALICE) == []
    granted = _ctx(server, ALICE, ("prompts:read",))
    assert _rendered(handle_prompts_get({"name": "p"}, granted)) == "hi"
    assert _prompt_names(server, ALICE, ("prompts:read",)) == ["p"]


def test_a_prompts_scope_gate_is_asked_before_its_checks() -> None:
    server = _server()
    check = Recording()
    server.register_prompt(name="p", render=lambda: "hi", scopes=["a", "b"], permissions=[check])

    _forbidden(handle_prompts_get({"name": "p"}, _ctx(server, ALICE, ("a",))), scopes=["a", "b"])
    assert check.asked == []


def test_a_prompt_with_neither_is_refused_under_the_setting(settings) -> None:
    server = _server(require=True, settings=settings)
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_prompt(name="p", render=lambda: "hi")
    assert str(raised.value) == (
        "MCP prompt 'p' is registered with no permissions and no scopes, so it answers any "
        "principal the transport authenticates. Pass permissions=[...] or scopes=[...] at "
        "registration. To downgrade this to a warning while you migrate, set "
        "SPEC_MCP['REQUIRE_TOOL_PERMISSIONS'] = False."
    )


def test_a_prompt_with_checks_alone_counts_as_guarded(settings) -> None:
    server = _server(require=True, settings=settings)
    server.register_prompt(name="p", render=lambda: "hi", permissions=[Unrestricted()])
    assert _prompt_names(server, ALICE) == ["p"]


def test_the_prompt_decorator_takes_both() -> None:
    server = _server()

    @server.prompt(scopes=["s"], permissions=[IsUser(ALICE)])
    def greet() -> str:
        return "hi"

    [binding] = server.prompts.all()
    assert binding.scopes == ("s",)
    assert [type(check) for check in binding.permissions] == [IsUser]


def test_a_deactivated_principal_is_refused_a_prompt_as_a_spec_refuses_one() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi", permissions=[Unrestricted()])
    gone = User(username="gone", is_active=False)

    _forbidden(handle_prompts_get({"name": "p"}, _ctx(server, gone)))
    assert _prompt_names(server, gone) == []
    assert _rendered(handle_prompts_get({"name": "p"}, _ctx(server, AnonymousUser()))) == "hi"


def test_an_anonymous_caller_reaches_the_check_as_an_anonymous_user() -> None:
    server = _server()
    check = Recording()
    server.register_prompt(name="p", render=lambda: "hi", permissions=[check])

    _rendered(handle_prompts_get({"name": "p"}, _ctx(server, None)))

    [(principal, _binding)] = check.asked
    assert isinstance(principal, AnonymousUser)


def test_a_prompt_completion_asks_the_prompts_checks() -> None:
    server = _server()
    check = Recording()
    binding = server.register_prompt(
        name="p",
        render=lambda topic: topic,
        arguments=[PromptArgument(name="topic")],
        completions={"topic": lambda value: ["alpha"]},
        permissions=[check, IsUser(ALICE)],
    )
    params = {
        "ref": {"type": "ref/prompt", "name": "p"},
        "argument": {"name": "topic", "value": ""},
    }

    done = handle_completion_complete(params, _ctx(server, ALICE))
    assert isinstance(done, dict)
    assert done["completion"]["values"] == ["alpha"]
    assert check.asked == [(ALICE, binding)]
    _forbidden(handle_completion_complete(params, _ctx(server, BOB)))


# ---------- UI resources ----------


def test_a_ui_resource_check_gates_its_listing_read_and_subscription() -> None:
    server = _server()
    check = Recording()
    binding = server.register_ui_resource(
        name="v", uri="ui://v", html="<html></html>", permissions=[check, IsUser(ALICE)]
    )

    read = handle_resources_read({"uri": "ui://v"}, _ctx(server, ALICE))
    assert isinstance(read, dict)
    assert read["contents"][0]["text"] == "<html></html>"
    assert check.asked == [(ALICE, binding)]
    assert _resource_uris(server, ALICE) == ["ui://v"]
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("ui://v",)), _ctx(server, ALICE)
    )
    assert granted.resource_uris == ("ui://v",)

    _forbidden(handle_resources_read({"uri": "ui://v"}, _ctx(server, BOB)))
    assert _resource_uris(server, BOB) == []
    refused, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("ui://v",)), _ctx(server, BOB)
    )
    assert refused.resource_uris == ()
    assert topics == frozenset()


async def test_an_async_ui_resource_read_asks_the_same_check() -> None:
    server = _server()
    server.register_ui_resource(
        name="v", uri="ui://v", html="<html></html>", permissions=[IsUser(ALICE)]
    )

    read = await handle_resources_read_async({"uri": "ui://v"}, _ctx(server, ALICE))
    assert isinstance(read, dict)
    _forbidden(await handle_resources_read_async({"uri": "ui://v"}, _ctx(server, BOB)))


def test_a_ui_resource_takes_a_scope_gate() -> None:
    server = _server()
    server.register_ui_resource(name="v", uri="ui://v", html="<html></html>", scopes="apps")

    _forbidden(handle_resources_read({"uri": "ui://v"}, _ctx(server, ALICE)), scopes=["apps"])
    assert _resource_uris(server, ALICE) == []
    assert _resource_uris(server, ALICE, ("apps",)) == ["ui://v"]


# ---------- what a binding refuses to hold ----------


class _OldStyle:
    """The transport permission this package no longer has: judged on the request and token."""

    def has_permission(self, request: Any, token: Any) -> bool:
        return True


class _Awaiting(PermissionCheck):
    async def has_permission(self, principal: Any, spec: Any) -> bool:  # ty: ignore[invalid-method-override]
        return True


class _HandsBackACoroutine(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> Any:
        async def later() -> bool:
            return True

        return later()


class _AwaitingObjectCheck(PermissionCheck):
    """A check whose ``has_object_permission`` is ``async def``: the kernel reads all three."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    async def has_object_permission(self, principal: Any, spec: Any, obj: Any) -> bool:
        return True


_NOT_A_SEQUENCE = "Prompt 'p'.permissions must be a sequence of PermissionCheck."


@pytest.mark.parametrize(
    ("permissions", "message"),
    [
        ("Unrestricted", _NOT_A_SEQUENCE),
        (b"Unrestricted", _NOT_A_SEQUENCE),
        ((check for check in [Unrestricted()]), _NOT_A_SEQUENCE),
        (
            [],
            "Prompt 'p'.permissions is empty, which would let anyone run it. Declare "
            "permissions=[Unrestricted()] to mean that.",
        ),
        (
            [_OldStyle()],
            "Prompt 'p'.permissions holds _OldStyle, which is not a PermissionCheck. "
            "Subclass PermissionCheck, or adapt it.",
        ),
        (
            [IsUser],
            "Prompt 'p'.permissions holds the class IsUser; pass an instance, IsUser().",
        ),
        (
            [_Awaiting()],
            "Prompt 'p'.permissions[0].has_permission is _Awaiting.has_permission, an async "
            "callable. It is called from sync code and never awaited, on both entry points; "
            "declare it with def, since adispatch already runs it off the event loop.",
        ),
        (
            [Unrestricted(), _AwaitingObjectCheck()],
            "Prompt 'p'.permissions[1].has_object_permission is "
            "_AwaitingObjectCheck.has_object_permission, an async callable. It is called from "
            "sync code and never awaited, on both entry points; declare it with def, since "
            "adispatch already runs it off the event loop.",
        ),
    ],
)
def test_a_prompt_refuses_what_is_not_a_check(permissions: Any, message: str) -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_prompt(name="p", render=lambda: "hi", permissions=permissions)
    assert str(raised.value) == message


class _NeedsCallContext(PermissionCheck):
    """A spec's check opting into ``call_context``, which only the kernel offers."""

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        return True


def test_a_check_requiring_a_kernel_keyword_cannot_gate_a_prompt() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi", permissions=[_NeedsCallContext()])
    with pytest.raises(TypeError, match="call_context"):
        handle_prompts_get({"name": "p"}, _ctx(server, ALICE))


def test_a_ui_resource_refuses_what_is_not_a_check() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_ui_resource(
            name="v", uri="ui://v", html="<html></html>", permissions=[_OldStyle()]
        )
    assert str(raised.value) == (
        "Resource 'v'.permissions holds _OldStyle, which is not a PermissionCheck. "
        "Subclass PermissionCheck, or adapt it."
    )


def test_an_answer_still_to_be_awaited_is_refused_not_read_as_a_pass() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda: "hi", permissions=[_HandsBackACoroutine()])

    with pytest.raises(ImproperlyConfigured) as raised:
        handle_prompts_get({"name": "p"}, _ctx(server, ALICE))
    assert str(raised.value) == (
        "_HandsBackACoroutine.has_permission answered with an awaitable (coroutine) where a "
        "bool was asked for. A permission check is sync-only and is never awaited, so the "
        "call is refused rather than allowed; declare the method with def."
    )


# ---------- a check that raises, in a filtered listing ----------


class _ReadsAMissingKey(PermissionCheck):
    """A check that cannot decide and raises, as one reading a value it was not handed does."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return {}["tenant"]


def test_a_prompt_check_that_raises_hides_that_prompt_and_no_other() -> None:
    # The listing failed whole, answered -32603, where the kernel arm hides
    # only the binding whose check raised.
    server = _server()
    server.register_prompt(name="open", render=lambda: "hi", permissions=[Unrestricted()])
    server.register_prompt(name="broken", render=lambda: "hi", permissions=[_ReadsAMissingKey()])
    server.register_prompt(name="also-open", render=lambda: "hi", permissions=[Unrestricted()])

    assert _prompt_names(server, ALICE) == ["open", "also-open"]


def test_a_view_check_that_raises_hides_that_view_and_no_other() -> None:
    server = _server()
    server.register_ui_resource(
        name="open", uri="ui://open", html="<html></html>", permissions=[Unrestricted()]
    )
    server.register_ui_resource(
        name="broken", uri="ui://broken", html="<html></html>", permissions=[_ReadsAMissingKey()]
    )

    assert _resource_uris(server, ALICE) == ["ui://open"]


def test_a_check_misconfigured_at_call_time_still_fails_the_listing() -> None:
    # A configuration error the get raises as well, so the listing fails with
    # it rather than hiding the prompt and leaving the error for later.
    server = _server()
    server.register_prompt(name="open", render=lambda: "hi", permissions=[Unrestricted()])
    server.register_prompt(name="p", render=lambda: "hi", permissions=[_HandsBackACoroutine()])

    with pytest.raises(ImproperlyConfigured) as raised:
        handle_prompts_list(None, _ctx(server, ALICE))
    assert str(raised.value) == (
        "_HandsBackACoroutine.has_permission answered with an awaitable (coroutine) where a "
        "bool was asked for. A permission check is sync-only and is never awaited, so the "
        "call is refused rather than allowed; declare the method with def."
    )
