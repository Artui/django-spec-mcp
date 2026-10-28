"""Smoke test: the tracked examples import + build against the shipped API.

The examples are standalone Django projects (their own settings + apps), so each
is exercised in a subprocess with its own ``DJANGO_SETTINGS_MODULE`` rather than
imported into this suite's Django context. ``build_server()`` performs every
tool / resource / prompt registration, so a stale API surfaces here as a
non-zero exit — the guard that would have caught the 0.8 ``filter_set=`` /
missing-``kind`` breakage.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_EXAMPLES = [
    pytest.param("invoicing", "invoicing.settings", "invoices.mcp", id="invoicing"),
    pytest.param("job_status", "job_status.settings", "jobs.mcp", id="job_status"),
]


# What the examples declare their operations with is the kernel, so nothing in
# them may reach for DRF or drf-services: the transport is the only part of the
# stack that may still name either, and it is not the examples' to name.
_NOT_THE_KERNEL = ("rest_framework", "rest_framework_services", "rest_framework_dataclasses")


def _foreign_imports(root: Path) -> list[str]:
    """``path: module`` for every import under ``root`` from DRF or drf-services."""
    found: list[str] = []
    for source in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            found.extend(
                f"{source.relative_to(root)}: {module}"
                for module in modules
                if module.split(".")[0] in _NOT_THE_KERNEL
            )
    return found


def test_no_example_imports_drf_or_drf_services() -> None:
    assert _foreign_imports(_EXAMPLES_DIR) == []


@pytest.mark.parametrize(("project", "settings_module", "mcp_module"), _EXAMPLES)
def test_example_build_server(project: str, settings_module: str, mcp_module: str) -> None:
    root = _EXAMPLES_DIR / project
    code = f"import django; django.setup()\nimport {mcp_module} as m\nm.build_server()"
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": settings_module}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# Runs inside the invoicing project: an in-memory database, three invoices, and
# two ``tools/call`` posts over the real JSON-RPC endpoint the project mounts.
# The replies are printed as JSON for the test to read, so the assertions stay
# here rather than in a string.
_INVOICING_FIELD_SELECTION = textwrap.dedent(
    """
    import json

    import django
    from django.conf import settings

    # Before setup, so no connection has read the on-disk name yet.
    settings.DATABASES["default"]["NAME"] = ":memory:"
    django.setup()

    from django.core.management import call_command
    from django.test import Client

    from invoices.models import Invoice

    call_command("migrate", verbosity=0)
    for number, amount in (("INV-A", 300), ("INV-B", 100), ("INV-C", 200)):
        Invoice.objects.create(number=number, amount_cents=amount)

    client = Client(HTTP_ORIGIN="http://localhost")
    initialized = client.post(
        "/mcp/",
        data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        }),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    headers = {
        "HTTP_MCP_PROTOCOL_VERSION": "2025-11-25",
        "HTTP_MCP_SESSION_ID": initialized["Mcp-Session-Id"],
    }

    def call(arguments):
        response = client.post(
            "/mcp/",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "invoices.list", "arguments": arguments},
            }),
            content_type="application/json",
            **headers,
        )
        return {"status": response.status_code, "body": response.json()}

    print(json.dumps({
        "rows": call({"fields": ["number"], "limit": 2}),
        "envelope": call({"fields": ["items"]}),
        "ordered": call({"ordering": ["-amount"], "min_amount": "150", "fields": ["number"]}),
    }))
    """
)


def test_invoicing_field_selection_is_per_item_and_refuses_the_envelope() -> None:
    """The example's paged list tool, selected per row and then against the envelope.

    Not only that the example runs: that it demonstrates what it claims. A row
    selection narrows each item, and a selection written against the page
    envelope (the shape the tool's ``outputSchema`` shows) is an ``isError``
    ``validation_error`` keyed by the argument, refused by the ``fields``
    shape's choices before the selector runs. The FilterSet's filters and its
    ordering narrow and sort the same page.
    """
    result = subprocess.run(
        [sys.executable, "-c", _INVOICING_FIELD_SELECTION],
        cwd=_EXAMPLES_DIR / "invoicing",
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "invoicing.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)

    rows = replies["rows"]
    assert rows["status"] == 200
    page = rows["body"]["result"]["structuredContent"]
    assert [set(item) for item in page["items"]] == [{"number"}, {"number"}]
    assert page["hasNext"] is True

    envelope = replies["envelope"]
    assert envelope["status"] == 200
    assert envelope["body"]["id"] == 2
    tool_result = envelope["body"]["result"]
    assert tool_result["isError"] is True
    assert json.loads(tool_result["content"][0]["text"])["error"] == {
        "type": "validation_error",
        "message": "Invalid arguments",
        "detail": {
            "fields": {"0": ["Select a valid choice. items is not one of the available choices."]}
        },
    }

    # ``min_amount`` drops INV-B, and ``ordering`` sorts by amount, largest first.
    ordered = replies["ordered"]["body"]["result"]["structuredContent"]
    assert ordered["items"] == [{"number": "INV-A"}, {"number": "INV-C"}]


# Runs inside the invoicing project, like the script above: the tool listing and
# ``tools/call`` posts, printed as JSON for the assertions below. ``post``
# reads ``result`` and nothing else, so a call answered with a JSON-RPC error
# fails the script rather than reaching an assertion; ``rpc`` is the whole
# reply, for the one read that is meant to be an error. The client half is
# shared by the three scripts that follow; each adds the calls it prints.
_INVOICING_CLIENT = textwrap.dedent(
    """
    import json
    import django
    from django.conf import settings

    settings.DATABASES["default"]["NAME"] = ":memory:"
    django.setup()

    from django.core.management import call_command
    from django.test import Client
    from invoices.models import Invoice

    call_command("migrate", verbosity=0)
    Invoice.objects.create(number="INV-A", amount_cents=100)
    Invoice.objects.create(number="INV-B", amount_cents=50, sent=True)
    client = Client(HTTP_ORIGIN="http://localhost")

    def rpc(method, params):
        response = client.post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="2026-07-28",
            HTTP_MCP_METHOD=method,
            **({"HTTP_MCP_NAME": params.get("name") or params["uri"]}
               if "name" in params or "uri" in params else {}),
        )
        return response.json()

    def post(method, params):
        return rpc(method, params)["result"]

    META = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "smoke", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    def call(name, arguments):
        return post("tools/call", {"name": name, "arguments": arguments, "_meta": META})

    listed = post("tools/list", {"_meta": META})
    tools = {tool["name"]: tool for tool in listed["tools"]}
    """
)
_INVOICING_LOOKUP_EMPTY_IDEMPOTENT_SEEDS = _INVOICING_CLIENT + textwrap.dedent(
    """
    print(json.dumps({
        "tools": tools,
        "set_amount": call("invoices.set_amount", {"number": "INV-A", "amount_cents": 250}),
        "no_number": call("invoices.set_amount", {"amount_cents": 250}),
        "find_miss": call("invoices.find", {"number": "INV-404"}),
        "outstanding": call("invoices.outstanding", {}),
        "currency": call("invoices.outstanding", {"currency": "USD"}),
        "unsent": call("invoices.unsent", {"sent": True}),
        "refused": call("invoices.create", {"number": "INV-C", "amount_cents": -5}),
    }))
    """
)


def test_invoicing_demonstrates_lookup_empty_result_idempotency_and_seeds() -> None:
    """Six behaviours the example registers, asserted as a client sees them.

    ``invoices.set_amount`` advertises its target lookup, requires it because
    the lookup has no default, refuses a call without it, lists
    ``idempotentHint``, and keeps a strict ``outputSchema`` because it has no
    output re-read, its refusal worded by the server's ``conventions=``; ``invoices.find`` answers a miss with ``{}`` under a
    schema that admits it; ``invoices.outstanding`` reads the mount's
    ``currency`` seed, does not advertise it, and refuses a client
    ``currency`` rather than read it; ``invoices.unsent`` binds ``sent`` with
    ``server_arguments=``, so it is not advertised and a client's ``sent`` is
    dropped; and ``invoices.create`` answers refused arguments with a
    ``validation_error`` result, not a JSON-RPC ``-32602``.
    """
    result = subprocess.run(
        [sys.executable, "-c", _INVOICING_LOOKUP_EMPTY_IDEMPOTENT_SEEDS],
        cwd=_EXAMPLES_DIR / "invoicing",
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "invoicing.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)
    tools = replies["tools"]

    set_amount = tools["invoices.set_amount"]
    assert set(set_amount["inputSchema"]["properties"]) == {"number", "amount_cents"}
    assert set(set_amount["inputSchema"]["required"]) == {"number", "amount_cents"}
    assert set_amount["annotations"]["idempotentHint"] is True
    assert replies["set_amount"]["structuredContent"]["amount_cents"] == 250
    # The lookup's ``number`` has no default, so a call leaving it out is
    # refused before the lookup would have raised ``TypeError``: named in the
    # message, and keyed in the detail the way a serializer keys a missing field.
    # The message is the example's ``conventions=``, served over the wire.
    no_number = replies["no_number"]
    assert no_number["isError"] is True
    assert json.loads(no_number["content"][0]["text"])["error"] == {
        "type": "validation_error",
        "message": (
            "Missing required argument(s): `number`. "
            "Look the invoice up with `invoices.list` if you do not have it."
        ),
        "detail": {"number": ["This field is required."]},
    }
    assert tools["invoices.find"]["inputSchema"]["required"] == ["number"]
    # A convention the example does not override keeps the package's words.
    assert tools["invoices.list"]["inputSchema"]["properties"]["fields"]["description"] == (
        "Invoice fields to return, e.g. id and number. On a paged result it "
        "applies to each item in `items`, never to the page envelope (`items`, `page`, "
        "`totalPages`, `hasNext`)."
    )

    find_schema = tools["invoices.find"]["outputSchema"]
    assert {"maxProperties": 0} in find_schema["anyOf"]
    assert replies["find_miss"]["structuredContent"] == {}
    assert not replies["find_miss"].get("isError")
    # A service with no output re-read selector renders its own return, which
    # cannot be filtered away, so its schema keeps a strict ``required``.
    set_amount_output = set_amount["outputSchema"]
    assert "anyOf" not in set_amount_output
    assert "number" in set_amount_output["required"]

    assert replies["outstanding"]["structuredContent"] == {
        "amount_cents": 250,
        "currency": "EUR",
    }
    # Filled by the server, so not asked of the client, and a client's own
    # ``currency`` is refused rather than read in the seed's place.
    assert "currency" not in tools["invoices.outstanding"]["inputSchema"]["properties"]
    assert replies["currency"]["isError"] is True
    assert json.loads(replies["currency"]["content"][0]["text"])["error"]["detail"] == {
        "currency": ["Unknown argument."]
    }

    # Bound by the mount's ``server_arguments``: not advertised, not required,
    # and a client's ``sent: true`` is dropped, so only the unsent invoice
    # comes back.
    unsent_schema = tools["invoices.unsent"]["inputSchema"]
    assert "sent" not in unsent_schema["properties"]
    assert "sent" not in unsent_schema.get("required", [])
    assert not replies["unsent"].get("isError"), replies["unsent"]
    assert [row["number"] for row in replies["unsent"]["structuredContent"]["items"]] == ["INV-A"]

    # Refused input is a tool execution error the model reads, with the
    # form's errors keyed by field under ``detail``.
    refused = replies["refused"]
    assert refused["isError"] is True
    error = json.loads(refused["content"][0]["text"])["error"]
    assert error == {
        "type": "validation_error",
        "message": "Invalid arguments",
        "detail": {"amount_cents": ["Ensure this value is greater than or equal to 0."]},
    }


_INVOICING_SPREAD_CLOSED_AND_EMPTY = _INVOICING_CLIENT + textwrap.dedent(
    """
    print(json.dumps({
        "tool": tools["invoices.apply_credit"],
        "partial": call("invoices.apply_credit", {"number": "INV-A", "credit_cents": 40}),
        "typo": call(
            "invoices.apply_credit", {"number": "INV-A", "credit_cents": 5, "memo": "goodwill"}
        ),
        "settles": call("invoices.apply_credit", {"number": "INV-B", "credit_cents": 50}),
        "remaining": dict(Invoice.objects.values_list("number", "amount_cents")),
    }))
    """
)


def test_invoicing_demonstrates_a_closed_spread_schema_and_a_declared_empty_result() -> None:
    """``invoices.apply_credit``, a service tool with no Validator.

    Its spec's ``reads`` are the service's own keywords, so the ``inputSchema``
    lists ``credit_cents`` beside the lookup's ``number`` and, under the default
    ``REJECT``, is closed: a call naming ``memo`` is a ``validation_error``
    keyed by it, and nothing ran. The spec declares
    ``allow_none=True`` with no output re-read, so its ``outputSchema`` admits
    the ``{}`` that a credit settling the invoice is served as.
    """
    result = subprocess.run(
        [sys.executable, "-c", _INVOICING_SPREAD_CLOSED_AND_EMPTY],
        cwd=_EXAMPLES_DIR / "invoicing",
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "invoicing.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)

    input_schema = replies["tool"]["inputSchema"]
    assert set(input_schema["properties"]) == {"number", "credit_cents"}
    assert set(input_schema["required"]) == {"number", "credit_cents"}
    assert input_schema["additionalProperties"] is False
    assert replies["partial"]["structuredContent"]["amount_cents"] == 60

    typo = replies["typo"]
    assert typo["isError"] is True
    assert json.loads(typo["content"][0]["text"])["error"] == {
        "type": "validation_error",
        "message": "Invalid arguments",
        "detail": {"memo": ["Unknown argument."]},
    }

    assert {"maxProperties": 0} in replies["tool"]["outputSchema"]["anyOf"]
    assert not replies["settles"].get("isError")
    assert replies["settles"]["structuredContent"] == {}
    # The refused call took nothing off; the settling one deleted ``INV-B``.
    assert replies["remaining"] == {"INV-A": 60}


_INVOICING_RESOURCE_PROMPT_AND_SEND = _INVOICING_CLIENT + textwrap.dedent(
    """
    pk = Invoice.objects.get(number="INV-A").pk
    print(json.dumps({
        "pk": pk,
        "read": post("resources/read", {"uri": f"invoices://{pk}", "_meta": META}),
        "missing": rpc("resources/read", {"uri": "invoices://999", "_meta": META}),
        "prompt": post(
            "prompts/get",
            {"name": "compose_invoice_email", "arguments": {"pk": str(pk)}, "_meta": META},
        ),
        "sent": call("invoices.mark_sent", {"pk": pk}),
        "again": call("invoices.mark_sent", {"pk": pk}),
    }))
    """
)


def test_invoicing_serves_its_resource_prompt_and_send_tool() -> None:
    """The ``invoice`` resource, the email prompt and ``invoices.mark_sent``.

    The resource reads one row through the URI template's ``pk``, presented by
    the same dataclass as the tools, and a URI naming no invoice is a missing
    resource; the prompt renders from the database; and
    marking an invoice sent twice is a ``service_error`` result the second time.
    """
    result = subprocess.run(
        [sys.executable, "-c", _INVOICING_RESOURCE_PROMPT_AND_SEND],
        cwd=_EXAMPLES_DIR / "invoicing",
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "invoicing.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)
    pk = replies["pk"]

    [contents] = replies["read"]["contents"]
    assert contents["uri"] == f"invoices://{pk}"
    assert contents["mimeType"] == "application/json"
    row = json.loads(contents["text"])
    assert {key: row[key] for key in ("id", "number", "amount_cents", "sent")} == {
        "id": pk,
        "number": "INV-A",
        "amount_cents": 100,
        "sent": False,
    }

    assert replies["missing"]["error"]["code"] == -32602
    assert replies["missing"]["error"]["message"] == "Resource not found: 'invoices://999'"

    [message] = replies["prompt"]["messages"]
    assert message["role"] == "user"
    assert "Invoice INV-A for $1.00 is now ready." in message["content"]["text"]

    assert replies["sent"]["structuredContent"]["sent"] is True
    again = replies["again"]
    assert again["isError"] is True
    assert json.loads(again["content"][0]["text"])["error"] == {
        "type": "service_error",
        "message": f"Invoice {pk} is already sent",
    }


# Runs inside the job_status project on a database file, not ``:memory:``: the
# job runs on a worker thread, which opens a connection of its own and would
# see an empty in-memory database. A modern-era call carries no session, so the
# worker has nobody to notify and only the row's status moves.
_JOB_STATUS = textwrap.dedent(
    """
    import json
    import sys
    import time

    import django
    from django.conf import settings

    settings.DATABASES["default"]["NAME"] = sys.argv[1]
    django.setup()

    from django.core.management import call_command
    from django.test import Client

    call_command("migrate", verbosity=0)
    client = Client(HTTP_ORIGIN="http://localhost")
    META = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "smoke", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    def rpc(method, params, name):
        response = client.post(
            "/mcp/",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": META},
            }),
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="2026-07-28",
            HTTP_MCP_METHOD=method,
            HTTP_MCP_NAME=name,
        )
        return response.json()

    def call(name, arguments):
        return rpc("tools/call", {"name": name, "arguments": arguments}, name)["result"]

    def read(uri):
        return rpc("resources/read", {"uri": uri}, uri)

    started = call("jobs.start", {"duration_seconds": 1})
    job_id = started["structuredContent"]["id"]
    # Bounded: a job of one second that has not finished in twenty is a failure.
    for _ in range(200):
        row = json.loads(read(f"jobs://{job_id}")["result"]["contents"][0]["text"])
        if row["status"] == "done":
            break
        time.sleep(0.1)
    print(json.dumps({
        "started": started,
        "refused": call("jobs.start", {"duration_seconds": 0}),
        "row": row,
        "done": call("jobs.list", {"status": "done"}),
        "queued": call("jobs.list", {"status": "queued"}),
        "missing": read("jobs://00000000-0000-0000-0000-000000000000"),
    }))
    """
)


def test_job_status_starts_a_job_and_serves_its_status(tmp_path: Path) -> None:
    """``jobs.start`` answers at once, the resource follows the job, ``jobs.list`` filters.

    The start returns the queued row with its id as a string, before the work
    runs; an out-of-range duration is a ``validation_error`` result; reading
    ``jobs://{job_id}`` follows the row to ``done``; the FilterSet's
    ``status`` narrows the list; and a job that does not exist is a missing
    resource.
    """
    result = subprocess.run(
        [sys.executable, "-c", _JOB_STATUS, str(tmp_path / "jobs.sqlite3")],
        cwd=_EXAMPLES_DIR / "job_status",
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "job_status.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)

    started = replies["started"]["structuredContent"]
    assert started["status"] == "queued"
    assert started["duration_seconds"] == 1
    assert started["finished_at"] is None

    refused = replies["refused"]
    assert refused["isError"] is True
    assert json.loads(refused["content"][0]["text"])["error"]["detail"] == {
        "duration_seconds": ["Ensure this value is greater than or equal to 1."]
    }

    row = replies["row"]
    assert row["id"] == started["id"]
    assert row["status"] == "done"
    assert row["finished_at"] is not None

    assert [item["id"] for item in replies["done"]["structuredContent"]["items"]] == [started["id"]]
    assert replies["queued"]["structuredContent"]["items"] == []

    assert replies["missing"]["error"]["code"] == -32602
