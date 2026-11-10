from __future__ import annotations

import ast
import importlib
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _package_imports(source: str) -> list[tuple[str, str]]:
    """``(module, symbol)`` for every ``from django_spec_mcp… import …``."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("django_spec_mcp"):
            found.extend((node.module or "", alias.name) for alias in node.names)
    return found


@pytest.mark.parametrize(
    "script", sorted(p.name for p in _SCRIPTS.glob("*.py")), ids=lambda name: name
)
def test_a_script_imports_only_module_paths_that_exist(script: str) -> None:
    # A script that imports nothing from the package (a docs checker driving
    for module, symbol in _package_imports((_SCRIPTS / script).read_text()):
        resolved = importlib.import_module(module)
        assert hasattr(resolved, symbol), f"{module} has no {symbol!r} ({script})"


@pytest.mark.parametrize(
    "script", sorted(p.name for p in _SCRIPTS.glob("*.py")), ids=lambda name: name
)
def test_a_script_imports_neither_drf_nor_drf_services(script: str) -> None:
    # The scripts drive kernel specs and have no use for either library, so a
    imported: list[str] = []
    for node in ast.walk(ast.parse((_SCRIPTS / script).read_text())):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert [
        module
        for module in imported
        if module.split(".")[0]
        in ("rest_framework", "rest_framework_services", "rest_framework_dataclasses")
    ] == []


def test_the_benchmark_times_three_paths_that_answer_the_call() -> None:
    # The benchmark checks each path answers ``{"result": 42}`` before timing
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS / "benchmark.py"), "--iterations", "5"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rows = [line.split("  ")[0] for line in result.stdout.splitlines()[2:]]
    assert rows == ["direct call", "dispatch", "HTTP round-trip"]
    assert "ToolWarning" not in result.stderr, result.stderr


# Imports the probe's module, which configures Django and mounts its server as
# the URL conf, then calls the tool through the test client the way the
# probe's client would: below the threshold, above it, and above it with the
# answer the question asked for.
_INTEROP_SERVER = textwrap.dedent(
    """
    import json

    from django.test import Client

    import interop_pydantic_ai as probe

    client = Client()
    era = "2026-07-28"
    meta = {
        "io.modelcontextprotocol/protocolVersion": era,
        "io.modelcontextprotocol/clientInfo": {"name": "smoke", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {}},
    }

    def call(arguments):
        return client.post(
            "/mcp/",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": probe.TOOL, "arguments": arguments, "_meta": meta},
            }),
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION=era,
            HTTP_MCP_METHOD="tools/call",
            HTTP_MCP_NAME=probe.TOOL,
        ).json()["result"]

    print(json.dumps({
        "threshold": probe.CONFIRM_ABOVE,
        "below": call({"count": 99}),
        "above": call({"count": 500}),
        "answered": call({"count": 500, "confirmed": True}),
    }))
    """
)


def test_the_interop_probe_serves_the_question_it_asks_a_client() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _INTEROP_SERVER],
        cwd=_SCRIPTS,
        env={**os.environ, "PYTHONPATH": str(_SCRIPTS)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)
    assert replies["threshold"] == 100

    assert replies["below"]["structuredContent"] == {"deleted": 99, "confirmed": False}

    above = replies["above"]
    assert above["resultType"] == "input_required"
    params = above["inputRequests"]["additionalInput"]["params"]
    assert params["message"] == "500 rows match. Confirm to proceed."
    assert params["requestedSchema"]["properties"] == {"confirmed": {"type": "boolean"}}

    # The retry carries the answer as one more argument, which the tool's
    assert replies["answered"]["structuredContent"] == {"deleted": 500, "confirmed": True}
