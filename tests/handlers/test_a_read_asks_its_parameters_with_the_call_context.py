from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django_service_specs import (
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from tests.utils import call_context

pytestmark = pytest.mark.django_db(transaction=True)


class _RecordsContext(PermissionCheck):
    """Admits, keeping the ``call_context`` the admission asked it with."""

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        self.contexts.append(call_context)
        return True


def _row(*, pk: int) -> dict[str, int]:
    return {"pk": pk}


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_read_reads_its_variables_under_the_call_context_dispatch_receives(
    is_async: bool,
) -> None:
    check = _RecordsContext()
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_row,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        permissions=[check],
    )
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(spec, name="row", uri_template="rows://{pk}")
    context = call_context(server)
    original = SelectorSpec.parameters

    with mock.patch.object(
        SelectorSpec, "parameters", autospec=True, side_effect=original
    ) as asked:
        if is_async:
            out = await handle_resources_read_async({"uri": "rows://7"}, context)
        else:
            out = await sync_to_async(handle_resources_read)({"uri": "rows://7"}, context)

    assert out["contents"][0]["text"] == '{\n  "pk": 7\n}', out
    # Asked once, by the admission, whose grant dispatch then runs under.
    (dispatched,) = check.contexts
    handed = [call.kwargs.get("call_context") for call in asked.call_args_list]
    assert len(handed) == 4, handed
    assert all(each is dispatched for each in handed), handed
