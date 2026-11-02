"""A watch of a kernel resource reads its URI variables as the read would.

``_kernel_read_admits`` runs dispatch's prelude alone, so it coerces the
variables against the declaration the call's ``call_context`` gives and hands
``check_call`` that same dict, as ``dispatch`` asks the declaration with the
dict it is handed. A ``RETRIEVE`` ``SelectorSpec`` takes the keyword and has
no Validator to hand it to, so what is pinned is the wiring.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from django_service_specs import (
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
)

from django_spec_mcp import MCPServer, SubscriptionFilter
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from tests.utils import call_context


class _RecordsContext(PermissionCheck):
    """Admits, keeping the ``call_context`` the prelude asked it with."""

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        self.contexts.append(call_context)
        return True


def _row(*, pk: int) -> dict[str, int]:
    return {"pk": pk}


def test_a_watch_reads_its_variables_under_the_call_context_its_checks_get() -> None:
    check = _RecordsContext()
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_row,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
            permissions=[check],
        ),
        name="row",
        uri_template="rows://{pk}",
    )
    original = SelectorSpec.parameters

    with mock.patch.object(
        SelectorSpec, "parameters", autospec=True, side_effect=original
    ) as asked:
        granted, _ = grant_subscription(
            SubscriptionFilter(resource_uris=("rows://7",)), call_context(server)
        )

    assert granted.resource_uris == ("rows://7",)
    (checked,) = check.contexts
    handed = [call.kwargs.get("call_context") for call in asked.call_args_list]
    # The watch's coercion and the prelude's shape check, each with the one dict.
    assert len(handed) == 2, handed
    assert all(each is checked for each in handed), handed
