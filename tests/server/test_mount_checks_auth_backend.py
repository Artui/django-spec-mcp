"""Mounting is where an auth backend becomes load-bearing, so it is checked there.

A server built without ``auth_backend=`` is a supported mode -- an in-process
server never authenticates anything -- so construction needs no backend. What is
refused is mounting one: every request to a mounted endpoint reaches
``authenticate``, and this package ships no backend that verifies tokens, so a
mount with none would either answer every caller or fail on the first request.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.server.mcp_server import MCPServer


def _refusal(name: str) -> str:
    return (
        f"MCPServer {name!r} is mounted with no auth_backend=. Every request to a "
        "mounted endpoint is authenticated by the backend, and this package ships "
        "none that verifies tokens, so there is nothing safe to fall back to. Pass "
        "auth_backend= a backend of your own (the authorization page shows one), or "
        "AllowAnyBackend() for local development."
    )


def test_constructing_without_a_backend_still_works() -> None:
    server = MCPServer(name="in-process")

    assert server.tools is not None
    assert server.auth_backend is None


def test_mounting_without_a_backend_refuses_by_name() -> None:
    server = MCPServer(name="mounted")

    with pytest.raises(ImproperlyConfigured) as caught:
        _ = server.urls

    assert str(caught.value) == _refusal("mounted")


def test_async_mounting_without_a_backend_refuses_by_name() -> None:
    server = MCPServer(name="mounted-async")

    with pytest.raises(ImproperlyConfigured) as caught:
        _ = server.async_urls

    assert str(caught.value) == _refusal("mounted-async")


def test_mounting_with_a_backend_serves_the_endpoint_and_its_metadata() -> None:
    server = MCPServer(name="allow-any", auth_backend=AllowAnyBackend())

    patterns, app_name, namespace = server.urls

    assert [p.name for p in patterns] == ["endpoint", "protected-resource-metadata"]
    assert (app_name, namespace) == ("mcp", "mcp")


class _SelfCheckingBackend(AllowAnyBackend):
    """A backend that asks to be checked when mounted, as a project's own may."""

    def __init__(self, *, fails: bool) -> None:
        self.fails = fails
        self.checked = 0

    def check_configuration(self) -> None:
        self.checked += 1
        if self.fails:
            raise ImproperlyConfigured("the backend cannot run")

    def authenticate(self, request: HttpRequest) -> None:
        return None


@pytest.mark.parametrize("attr", ["urls", "async_urls"])
def test_a_self_checking_backend_is_asked_once_at_the_mount(attr: str) -> None:
    backend = _SelfCheckingBackend(fails=False)
    server = MCPServer(name="checked", auth_backend=backend)

    assert backend.checked == 0
    _ = getattr(server, attr)

    assert backend.checked == 1


@pytest.mark.parametrize("attr", ["urls", "async_urls"])
def test_a_self_checking_backend_that_fails_stops_the_mount(attr: str) -> None:
    server = MCPServer(name="unchecked", auth_backend=_SelfCheckingBackend(fails=True))

    with pytest.raises(ImproperlyConfigured) as caught:
        _ = getattr(server, attr)

    assert str(caught.value) == "the backend cannot run"
