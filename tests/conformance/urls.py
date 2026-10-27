"""URL conf for the conformance suite — the full surface, in one place.

Mounted via ``@pytest.mark.urls("tests.conformance.urls")`` on conformance tests
so each test's Django client can drive the actual URL resolution rather than
calling views directly.
"""

from __future__ import annotations

from typing import Any

from django.urls import path

from tests.conformance.mcp import build_conformance_server


def build_conformance_urls() -> list[Any]:
    server = build_conformance_server()
    return [path("mcp/", server.urls)]


def __getattr__(name: str) -> Any:
    """Build ``urlpatterns`` on each read, so each test resolves a server of its own.

    ``@pytest.mark.urls("tests.conformance.urls")`` names this module, which is
    imported once; a ``urlpatterns`` assigned at import was therefore one
    server, session store included, shared by every test mounting this conf. A
    session one test minted was honoured in the next. Django reads the
    attribute once per resolver and pytest-django clears the resolver cache
    around every test, so a module-level ``__getattr__`` (PEP 562) gives each
    test a fresh server while one test's requests keep sharing its own
    (``test_a_session_minted_before_the_url_caches_clear_is_unknown_after``).
    """
    if name == "urlpatterns":
        return build_conformance_urls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
