from __future__ import annotations

from typing import Any

from django.urls import path

from tests.elicitation.conftest import build_kernel_server

SERVER = build_kernel_server()

urlpatterns: list[Any] = [path("mcp/", SERVER.urls)]
