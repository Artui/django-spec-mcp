"""A mounted server whose ``rows.delete`` needs a confirmation, for the HTTP suite.

The conftest's kernel server, mounted once at import as a deployment would
mount it, so the retry of an input round trip reaches the same server as a
second, independent request.
"""

from __future__ import annotations

from typing import Any

from django.urls import path

from tests.elicitation.conftest import build_kernel_server

SERVER = build_kernel_server()

urlpatterns: list[Any] = [path("mcp/", SERVER.urls)]
