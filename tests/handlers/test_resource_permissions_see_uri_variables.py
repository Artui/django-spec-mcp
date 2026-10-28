"""A kernel resource's permission reading ``arguments`` judges the URI a read names.

A kernel ``PermissionCheck`` opts into the call's ``arguments`` by declaring the
keyword, and the read hands the URI-template variables there, read into the
types the spec's ``reads`` declare, so ``projects://7`` reaches a check as
``{"project_pk": 7}``, in the class-level check and in the object guard. One
that declares ``call_context`` reads the request and the resource's name off
it, so both checks see one request.

Where the spec's check answers: a denied caller is charged nothing. A kernel
resource's checks are the kernel's, which ``dispatch`` asks after the shell has
charged the rate limits, so the shell asks them itself first
(``admit_kernel_read``) and dispatch runs under the grant they gave. The last
test pins that order.

The subscription half asks the same: ``grant_subscription`` grants a watch only
on the URIs the read would admit, judged by the spec's own checks.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_service_specs import Parameter, Parameters, PermissionCheck, SelectorKind, SelectorSpec

from django_spec_mcp import MCPServer, SubscriptionFilter
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import RefusingRateLimit


class _GrantsProjectSeven(PermissionCheck):
    """Admits a read of project 7 alone, recording what each check was handed."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def has_permission(
        self, principal: Any, spec: Any, *, arguments: Any = None, call_context: Any = None
    ) -> bool:
        self.seen.append((None if arguments is None else dict(arguments), call_context["action"]))
        return arguments is not None and arguments.get("project_pk") == 7


def _project(*, project_pk: int) -> dict[str, int]:
    return {"project": project_pk}


def _server(check: PermissionCheck, *rate_limits: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_project,
            reads=Parameters([Parameter("project_pk", "integer", required=True)]),
            permissions=[check],
        ),
        name="project",
        uri_template="projects://{project_pk}",
        rate_limits=list(rate_limits),
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


async def _read(server: MCPServer, uri: str, *, is_async: bool) -> Any:
    if is_async:
        return await handle_resources_read_async({"uri": uri}, _ctx(server))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_resources_read)({"uri": uri}, _ctx(server))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_resource_permission_sees_the_uri_variables_of_the_read(is_async: bool) -> None:
    check = _GrantsProjectSeven()

    out = await _read(_server(check), "projects://7", is_async=is_async)

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"
    assert out["contents"]
    assert check.seen == [({"project_pk": 7}, "project")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_resource_permission_still_denies_a_uri_it_does_not_grant(is_async: bool) -> None:
    check = _GrantsProjectSeven()

    out = await _read(_server(check), "projects://8", is_async=is_async)

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    assert check.seen == [({"project_pk": 8}, "project")]


class _ReadsTheRequest(PermissionCheck):
    """Admits only the request the transport received, carrying the caller's token."""

    def __init__(self, request: HttpRequest, token: TokenInfo) -> None:
        self.request = request
        self.token = token

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any = None) -> bool:
        request = call_context["request"]
        return request is self.request and request.mcp_token is self.token


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_resource_permission_reads_the_request_the_read_arrived_on(
    is_async: bool,
) -> None:
    request, token = HttpRequest(), TokenInfo(user=None)
    server = _server(_ReadsTheRequest(request, token))
    context = MCPCallContext(
        http_request=request,
        token=token,
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    params: dict[str, Any] = {"uri": "projects://7"}

    if is_async:
        out = await handle_resources_read_async(params, context)
    else:
        out = await sync_to_async(handle_resources_read)(params, context)

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("uri", "code", "consumed"),
    [
        ("projects://8", JsonRpcErrorCode.FORBIDDEN, 0),
        ("projects://7", JsonRpcErrorCode.RATE_LIMITED, 1),
    ],
    ids=["denied", "admitted"],
)
async def test_the_specs_check_answers_before_the_rate_limit(
    is_async: bool, uri: str, code: JsonRpcErrorCode, consumed: int
) -> None:
    # A denied caller is refused and charged nothing, and an admitted one is
    # the one the rate limit then refuses, the check asked once either way.
    limiter = RefusingRateLimit()
    check = _GrantsProjectSeven()

    out = await _read(_server(check, limiter), uri, is_async=is_async)

    assert isinstance(out, JsonRpcError)
    assert out.code == code
    assert limiter.consumed == consumed
    assert len(check.seen) == 1


def test_a_subscription_is_granted_the_uris_the_read_would_admit() -> None:
    check = _GrantsProjectSeven()
    server = _server(check)

    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("projects://7", "projects://8")), _ctx(server)
    )

    assert granted.resource_uris == ("projects://7",)
    assert check.seen == [({"project_pk": 7}, "project"), ({"project_pk": 8}, "project")]
