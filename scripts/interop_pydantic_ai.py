"""Drive this server with a real Pydantic-AI ``MCPToolset``, over a real socket.

The claim this settles is the one nothing in ``tests/`` can: our elicitation
surface is the result-carried form, where the question rides in a ``tools/call``
result and the client retries the original call. Every test in this repo asserts
that shape against our own reading of the spec. Only a foreign client proves
another implementation reads it the same way.

It is a *pairing* test rather than a version check. The client's MCP SDK decides
which protocol era it speaks, and this server answers both, so the script works
out which era it got and asserts the behaviour documented for that era:

- **modern SDK** — the call is answered with ``resultType: "input_required"``,
  the client's elicitation handler is invoked, the retry lands, and the tool
  returns the value the answer unlocked.
- **legacy SDK** — no era carries the question, so the call degrades to an
  ordinary error result naming what is missing, and the handler is never called.

Run it against whatever is installed:

    uv run --prerelease=allow python scripts/interop_pydantic_ai.py

Nothing here is imported by the package or the test suite; it is a standalone
probe, run on a schedule by ``.github/workflows/upstream-drift.yml``.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import warnings
from dataclasses import dataclass
from typing import Any
from wsgiref.simple_server import WSGIServer, make_server

import django
from django.conf import settings
from django.urls import path

TOOL: str = "rows.delete"
CONFIRM_ABOVE: int = 100


def configure() -> None:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="interop-probe-only",
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        MIDDLEWARE=["django.middleware.common.CommonMiddleware"],
        ROOT_URLCONF=__name__,
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        # Deliberately not widened. A non-browser client sends no Origin, so the
        # shipped default has to be enough -- if it ever stops being, the docs
        # are wrong and this is where we should find out.
        SPEC_MCP={},
    )
    django.setup()


configure()

from django_service_specs import (  # noqa: E402
    AdditionalInputRequired,
    DataclassValidator,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import InMemorySessionStore, MCPServer  # noqa: E402
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend  # noqa: E402


@dataclass
class DeleteInput:
    """The tool's arguments. ``confirmed`` is optional, so the retry carrying
    the answer is an ordinary call with one more argument."""

    count: int
    confirmed: bool = False


def delete_rows(*, count: int, confirmed: bool) -> dict[str, Any]:
    """Cheap below the threshold, and something you would want to be asked about
    above it -- the whole reason the elicitation exchange exists."""
    if count > CONFIRM_ABOVE and not confirmed:
        raise AdditionalInputRequired(
            f"{count} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    return {"deleted": count, "confirmed": confirmed}


def build_server() -> MCPServer:
    server = MCPServer(
        name="interop-probe",
        version="0.0.1",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_tool(
        ServiceSpec(
            service=delete_rows,
            validator=DataclassValidator(DeleteInput),
            atomic=False,
            permissions=[Unrestricted()],
        ),
        name=TOOL,
        description="Delete rows, asking for confirmation past a threshold.",
    )
    return server


urlpatterns = [path("mcp/", build_server().urls)]


def serve() -> tuple[WSGIServer, str]:
    """Start the endpoint on a free port, in a daemon thread."""
    from django.core.wsgi import get_wsgi_application

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    httpd = make_server("127.0.0.1", port, get_wsgi_application())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/mcp/"


def client_speaks_modern() -> bool:
    """Whether the installed MCP SDK knows the era that carries the question.

    The stateless revision moved the types into their own distribution, so its
    presence is the marker -- and a marker beats a version comparison, which
    would need updating for every SDK release that changes nothing.
    """
    try:
        from mcp_types.version import MODERN_PROTOCOL_VERSIONS
    except ImportError:
        return False
    return bool(MODERN_PROTOCOL_VERSIONS)


async def probe(url: str) -> None:
    from pydantic_ai.mcp import MCPToolset

    asked: list[str] = []

    async def elicitation_handler(
        message: str,
        response_type: Any,
        params: Any,
        context: Any,
    ) -> dict[str, Any]:
        asked.append(message)
        return {"confirmed": True}

    with warnings.catch_warnings():
        # Pydantic-AI warns that the handler "will never be called" on a modern
        # session, on the reasoning that the server has no connection to issue a
        # request over. True of the mechanism it was written for; this one needs
        # no connection, because the question arrives inside a result. Asserting
        # that below is precisely the point, so the warning cannot be fatal here.
        warnings.simplefilter("ignore", UserWarning)
        toolset = MCPToolset(url, elicitation_handler=elicitation_handler)
        async with toolset:
            names = [tool.name for tool in await toolset.list_tools()]
            assert names == [TOOL], f"expected [{TOOL!r}], got {names!r}"

            below = await toolset.direct_call_tool(TOOL, {"count": CONFIRM_ABOVE - 1})
            assert below == {"deleted": CONFIRM_ABOVE - 1, "confirmed": False}, below
            assert not asked, f"asked a question it had no reason to ask: {asked!r}"

            above = CONFIRM_ABOVE * 5
            if client_speaks_modern():
                result = await toolset.direct_call_tool(TOOL, {"count": above})
                assert asked == [f"{above} rows match. Confirm to proceed."], asked
                assert result == {"deleted": above, "confirmed": True}, result
                print(f"OK: the question was asked and answered ({asked[0]!r})")
            else:
                try:
                    result = await toolset.direct_call_tool(TOOL, {"count": above})
                except Exception as exc:
                    text = str(exc)
                else:
                    raise AssertionError(f"expected a degraded error result, got {result!r}")
                assert "input_required" in text, text
                assert "confirmed" in text, text
                assert not asked, f"a legacy client cannot be asked, yet was: {asked!r}"
                print("OK: degraded to an error result naming the missing input")


def main() -> int:
    httpd, url = serve()
    try:
        import fastmcp
        import pydantic_ai

        era = "modern" if client_speaks_modern() else "legacy"
        print(f"pydantic-ai {pydantic_ai.__version__}, fastmcp {fastmcp.__version__}, {era} era")
        asyncio.run(probe(url))
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
