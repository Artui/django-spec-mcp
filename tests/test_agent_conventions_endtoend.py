from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.tasks.create_task import create_task
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor
from tests.testapp.conventions import conventions_server
from tests.testapp.models import Invoice
from tests.utils import tool_error

DEFAULTS = AgentConventions()

# How many times each line appears in ``_observe``: the handle wording once per
# tool (both present a handle), the scope sentence on the param, the
# missing-argument message from ``acall_tool`` and ``call_tool``.
WHERE: dict[str, int] = {
    "handle_field_description": 2,
    "handle_line": 2,
    "query_param_on_pages": 1,
    "missing_arguments": 2,
}


async def _observe(server: MCPServer) -> dict[str, Any]:
    """Everything the server says that a convention could reach."""
    return {
        "listing": await sync_to_async(server.list_tools)(user=None),
        "page": await server.acall_tool("invoices.list", {}, user=None),
        "missing": await server.acall_tool("invoices.rename", {"number": "X"}, user=None),
        "missing_in_process": (
            await sync_to_async(server.call_tool)("invoices.rename", {"number": "X"}, user=None)
        ).to_dict(),
    }


def _rendered(text: str) -> str:
    """``text`` as it reads once served: the one placeholder filled for ``pk``."""
    return text.replace("{names}", "`pk`")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("field", list(WHERE))
async def test_each_field_lands_where_it_is_written_and_only_there(field: str) -> None:
    await Invoice.objects.acreate(number="INV-1")
    sentinel = f"Sentinel for {field}: {{names}}." if field == "missing_arguments" else field
    default_text = _rendered(getattr(DEFAULTS, field))

    default = json.dumps(await _observe(conventions_server()), sort_keys=True)
    custom = json.dumps(
        await _observe(conventions_server(AgentConventions(**{field: sentinel}))),
        sort_keys=True,
    )

    # Present where the table says, so the replacement below is not vacuous.
    assert default.count(default_text) == WHERE[field]
    assert custom.count(_rendered(sentinel)) == WHERE[field]
    assert custom == default.replace(default_text, _rendered(sentinel))


@pytest.mark.django_db(transaction=True)
async def test_none_drops_the_handle_wording_and_the_scope_sentence() -> None:
    await Invoice.objects.acreate(number="INV-1")
    server = conventions_server(
        AgentConventions(handle_field_description=None, handle_line=None, query_param_on_pages=None)
    )

    seen = await _observe(server)

    tools = {tool["name"]: tool for tool in seen["listing"]["tools"]}
    assert tools["invoices.list"]["description"] == "List invoices."
    assert tools["invoices.rename"]["description"] == "Rename an invoice."
    assert "description" not in tools["invoices.rename"]["outputSchema"]["properties"]["id"]
    item = tools["invoices.list"]["outputSchema"]["properties"]["items"]["items"]
    assert "description" not in item["properties"]["id"]
    fields = tools["invoices.list"]["inputSchema"]["properties"]["fields"]
    assert fields["description"] == "Fields to return."


@pytest.mark.django_db(transaction=True)
async def test_a_doubled_brace_reaches_the_model_as_one_in_every_field() -> None:
    # Every field is a ``str.format`` template, rendered whether or not it has a
    await Invoice.objects.acreate(number="INV-1")
    server = conventions_server(
        AgentConventions(
            handle_field_description="Handle {{d}}.",
            handle_line="Line {{l}}.",
            query_param_on_pages="Scope {{s}}.",
            missing_arguments="Missing {{m}}: {names}.",
        )
    )

    seen = await _observe(server)

    tools = {tool["name"]: tool for tool in seen["listing"]["tools"]}
    listed, rename = tools["invoices.list"], tools["invoices.rename"]
    assert rename["outputSchema"]["properties"]["id"]["description"] == "Handle {d}."
    item = listed["outputSchema"]["properties"]["items"]["items"]
    assert item["properties"]["id"]["description"] == "Handle {d}."
    assert rename["description"] == "Rename an invoice.\n\nLine {l}."
    assert listed["description"] == "List invoices.\n\nLine {l}."
    fields = listed["inputSchema"]["properties"]["fields"]
    assert fields["description"] == "Fields to return. Scope {s}."
    assert tool_error(seen["missing"])["message"] == "Missing {m}: `pk`."
    assert tool_error(seen["missing_in_process"])["message"] == "Missing {m}: `pk`."


@pytest.mark.django_db(transaction=True)
async def test_two_servers_in_one_process_keep_their_own_wording() -> None:
    # Conventions are instance state: nothing is read from, or written to, the
    await Invoice.objects.acreate(number="INV-1")
    first = conventions_server(
        AgentConventions(handle_line="First line.", missing_arguments="First: {names}.")
    )
    second = conventions_server(
        AgentConventions(handle_line="Second line.", missing_arguments="Second: {names}.")
    )
    default = conventions_server()

    seen = [await _observe(server) for server in (first, second, default, first)]

    lines = [{tool["description"] for tool in observed["listing"]["tools"]} for observed in seen]
    assert (
        lines[0]
        == lines[3]
        == {
            "List invoices.\n\nFirst line.",
            "Rename an invoice.\n\nFirst line.",
        }
    )
    assert lines[1] == {"List invoices.\n\nSecond line.", "Rename an invoice.\n\nSecond line."}
    assert lines[2] == {
        f"List invoices.\n\n{DEFAULTS.handle_line}",
        f"Rename an invoice.\n\n{DEFAULTS.handle_line}",
    }
    messages = [tool_error(observed["missing"])["message"] for observed in seen]
    assert messages == [
        "First: `pk`.",
        "Second: `pk`.",
        "Missing required argument(s): `pk`.",
        "First: `pk`.",
    ]


@pytest.mark.django_db
def test_a_task_runs_under_its_servers_conventions() -> None:
    # A task runs off the request path, in a context the server rebuilds for
    Invoice.objects.create(number="INV-1")
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = conventions_server(
        AgentConventions(missing_arguments="Task: {names}."),
        task_store=store,
        task_executor=executor,
    )

    def run(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        task = create_task(
            store=store,
            executor=executor,
            tool_name=tool_name,
            arguments=arguments,
            token=TokenInfo(user=None),
            ttl_ms=60_000,
            poll_interval_ms=500,
        )
        server.run_task(task.task_id)
        return tool_error(store.get(task.task_id).task.result)

    assert run("invoices.rename", {"number": "X"})["message"] == "Task: `pk`."


def test_a_server_keeps_the_conventions_it_was_given() -> None:
    conventions = AgentConventions(handle_line="Mine.")

    assert conventions_server(conventions).conventions is conventions
    assert conventions_server().conventions == AgentConventions()
