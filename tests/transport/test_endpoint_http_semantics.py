"""What a client sees of the three endpoints at the HTTP level, before any JSON-RPC.

The MCP endpoint (on both mounts) and the protected-resource metadata view are
answered for ``HEAD`` and ``OPTIONS``, opt out of CSRF and of
``LoginRequiredMiddleware``, refuse an ``Accept`` header admitting nothing they
can answer with, and put an ``Allow`` header on every ``405``. None of that is
written in this package's handlers: each was a property of the endpoints being
DRF viewsets, so these tests were written against the viewsets first and hold
the plain Django views to the same answers.

Two things are deliberately left out, because they were DRF's and are not this
package's to keep: the body of an ``OPTIONS`` answer (DRF served its metadata,
the class docstring included) and the ``Allow`` header on answers that are not a
``405``.

The async client sends request headers through ``headers=`` only: an
``HTTP_ACCEPT`` keyword lands in the ASGI scope, not in the request's headers,
so a test written that way would pass without sending what it names.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django import VERSION as DJANGO_VERSION
from django.contrib.auth import get_user_model
from django.test import AsyncClient, Client, RequestFactory, override_settings

from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.mcp import build_server
from tests.testapp.urlconf_for import urlconf_for

_ENDPOINT = "/mcp/"
_PRM = "/mcp/.well-known/oauth-protected-resource"
_ENDPOINT_ALLOW = "GET, POST, DELETE, HEAD, OPTIONS"
_PRM_ALLOW = "GET, HEAD, OPTIONS"
_NOT_ACCEPTABLE = {"detail": "Could not satisfy the request Accept header."}
_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"},
        },
    }
)

# Signed-cookie sessions, because the suite installs no sessions app: a
# session cookie is all these tests need, and that engine needs no table.
_SESSION_SETTINGS: dict[str, Any] = {
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
}
_SESSION_MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def mount(request: pytest.FixtureRequest) -> Any:
    """Mount the test server on the sync or the async URL conf."""
    is_async: bool = request.param
    with override_settings(ROOT_URLCONF=urlconf_for(build_server(), is_async=is_async)):
        yield is_async


async def _send(is_async: bool, method: str, path: str, **kwargs: Any) -> Any:
    """Send one request on the mount's own client, the sync one off the event loop."""
    if is_async:
        return await getattr(AsyncClient(), method)(path, **kwargs)
    return await sync_to_async(getattr(Client(), method))(path, **kwargs)


def _post_initialize(client: Client, **headers: str) -> Any:
    return client.post(_ENDPOINT, _INITIALIZE, content_type="application/json", headers=headers)


async def _apost_initialize(client: AsyncClient, **headers: str) -> Any:
    return await client.post(
        _ENDPOINT, _INITIALIZE, content_type="application/json", headers=headers
    )


# ----- HEAD -----


def test_head_on_the_sync_endpoint_is_its_get_a_405_naming_the_methods() -> None:
    """WSGI serves no stream, so ``HEAD`` answers what ``GET`` does."""
    response = Client().head(_ENDPOINT)
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW
    assert response.content == b""


@pytest.mark.django_db(transaction=True)
async def test_head_on_the_async_endpoint_is_its_get_refused_without_a_session() -> None:
    """``HEAD`` takes ``GET``'s path, which with no session opens nothing."""
    with override_settings(ROOT_URLCONF=urlconf_for(build_server(), is_async=True)):
        response = await AsyncClient().head(
            _ENDPOINT, headers={"Mcp-Protocol-Version": "2025-11-25"}
        )
    assert response.status_code == 404
    assert response.content == b""


async def test_head_on_the_protected_resource_metadata_is_an_empty_200(mount: bool) -> None:
    response = await _send(mount, "head", _PRM)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.content == b""


# ----- OPTIONS -----


async def test_options_on_the_endpoint_names_its_methods(mount: bool) -> None:
    response = await _send(mount, "options", _ENDPOINT)
    assert response.status_code == 200
    assert response["Allow"] == _ENDPOINT_ALLOW


async def test_options_on_the_protected_resource_metadata_names_its_methods(mount: bool) -> None:
    response = await _send(mount, "options", _PRM)
    assert response.status_code == 200
    assert response["Allow"] == _PRM_ALLOW


# ----- the protected-resource metadata -----


async def test_the_protected_resource_metadata_is_json_and_never_cached(mount: bool) -> None:
    response = await _send(mount, "get", _PRM)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    # Compared parsed: the bytes' separators are the encoder's, not the RFC's.
    assert (
        json.loads(response.content)
        == build_server()._auth_backend.protected_resource_metadata().to_dict()
    )  # noqa: SLF001
    assert "no-store" in response["Cache-Control"]
    assert "Expires" in response


async def test_a_post_to_the_protected_resource_metadata_is_a_405_naming_its_methods(
    mount: bool,
) -> None:
    response = await _send(mount, "post", _PRM)
    assert response.status_code == 405
    assert response["Allow"] == _PRM_ALLOW


# ----- Allow on every 405 -----


async def test_an_unrouted_method_is_a_405_naming_the_methods(mount: bool) -> None:
    response = await _send(mount, "put", _ENDPOINT)
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW


def test_the_sync_get_405_names_the_methods() -> None:
    response = Client().get(_ENDPOINT)
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW


async def test_a_delete_with_sessions_off_is_a_405_naming_the_methods(mount: bool) -> None:
    server = build_server(config=build_mcp_config(sessions_enabled=False))
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=mount)):
        response = await _send(mount, "delete", _ENDPOINT)
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW


async def test_a_modern_era_delete_is_a_405_naming_the_methods(mount: bool) -> None:
    response = await _send(
        mount, "delete", _ENDPOINT, headers={"Mcp-Protocol-Version": "2026-07-28"}
    )
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW


async def test_a_get_with_sessions_off_is_a_405_naming_the_methods(mount: bool) -> None:
    """With no session there is no channel to open a stream on, on either mount."""
    server = build_server(config=build_mcp_config(sessions_enabled=False))
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=mount)):
        response = await _send(mount, "get", _ENDPOINT)
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW


async def test_a_modern_era_get_is_a_405_naming_the_methods(mount: bool) -> None:
    response = await _send(mount, "get", _ENDPOINT, headers={"Mcp-Protocol-Version": "2026-07-28"})
    assert response.status_code == 405
    assert response["Allow"] == _ENDPOINT_ALLOW


# ----- Accept -----


@pytest.mark.parametrize(
    "accept",
    [None, "*/*", "application/*", "application/json", "application/json, text/event-stream"],
)
async def test_an_accept_admitting_json_is_served(mount: bool, accept: str | None) -> None:
    headers: dict[str, str] = {} if accept is None else {"Accept": accept}
    if mount:
        response = await _apost_initialize(AsyncClient(), **headers)
    else:
        response = await sync_to_async(_post_initialize)(Client(), **headers)
    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/json"


@pytest.mark.parametrize("accept", ["text/html", ""])
@pytest.mark.parametrize("method", ["post", "delete", "options"])
async def test_an_accept_admitting_nothing_the_endpoint_serves_is_a_406(
    mount: bool, accept: str, method: str
) -> None:
    response = await _send(
        mount,
        method,
        _ENDPOINT,
        data=_INITIALIZE,
        content_type="application/json",
        headers={"Accept": accept},
    )
    assert response.status_code == 406
    assert response["Content-Type"] == "application/json"
    assert json.loads(response.content) == _NOT_ACCEPTABLE


async def test_the_protected_resource_metadata_refuses_an_accept_it_cannot_serve(
    mount: bool,
) -> None:
    response = await _send(mount, "get", _PRM, headers={"Accept": "text/html"})
    assert response.status_code == 406
    assert json.loads(response.content) == _NOT_ACCEPTABLE


def test_the_sync_endpoint_refuses_an_accept_naming_only_a_stream() -> None:
    """WSGI never streams, so a client accepting only SSE cannot be answered."""
    response = _post_initialize(Client(), Accept="text/event-stream")
    assert response.status_code == 406
    assert json.loads(response.content) == _NOT_ACCEPTABLE


# ----- CSRF and login-required opt-outs -----


def _initialize_then_delete(user: Any) -> tuple[Any, Any]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    initialized = _post_initialize(client)
    deleted = client.delete(
        _ENDPOINT,
        headers={
            "Mcp-Protocol-Version": "2025-11-25",
            "Mcp-Session-Id": initialized["Mcp-Session-Id"],
        },
    )
    return initialized, deleted


@pytest.mark.django_db(transaction=True)
async def test_a_session_cookie_does_not_bring_csrf_to_the_endpoint(mount: bool) -> None:
    """A bearer-token transport has no CSRF token to send, cookie or not.

    The cookie is what makes ``CsrfViewMiddleware`` enforce at all on a
    request it would otherwise also refuse, so the test logs a user in first.
    """
    user = await get_user_model().objects.acreate(username="cookie-holder")
    with override_settings(MIDDLEWARE=_SESSION_MIDDLEWARE, **_SESSION_SETTINGS):
        if mount:
            client: Any = AsyncClient(enforce_csrf_checks=True)
            await sync_to_async(client.force_login)(user)
            initialized = await _apost_initialize(client)
            deleted = await client.delete(
                _ENDPOINT,
                headers={
                    "Mcp-Protocol-Version": "2025-11-25",
                    "Mcp-Session-Id": initialized["Mcp-Session-Id"],
                },
            )
        else:
            initialized, deleted = await sync_to_async(_initialize_then_delete)(user)
    assert initialized.status_code == 200, initialized.content
    assert deleted.status_code == 204


async def test_csrf_never_answers_for_the_protected_resource_metadata(mount: bool) -> None:
    """A POST there is the view's own ``405``, not ``CsrfViewMiddleware``'s ``403``.

    The metadata only answers ``GET``, so the opt-out shows only as which
    refusal a POST gets: without it the middleware refuses first.
    """
    with override_settings(MIDDLEWARE=_SESSION_MIDDLEWARE, **_SESSION_SETTINGS):
        if mount:
            response = await AsyncClient(enforce_csrf_checks=True).post(_PRM)
        else:
            response = await sync_to_async(Client(enforce_csrf_checks=True).post)(_PRM)
    assert response.status_code == 405, response.content[:200]
    assert response["Allow"] == _PRM_ALLOW


@pytest.mark.skipif(DJANGO_VERSION < (5, 1), reason="LoginRequiredMiddleware lands in Django 5.1")
@pytest.mark.parametrize("path", [_ENDPOINT, _PRM])
async def test_login_required_middleware_never_redirects_an_endpoint(
    mount: bool, path: str
) -> None:
    """The metadata is public discovery, and the endpoint authenticates itself."""
    middleware = [*_SESSION_MIDDLEWARE, "django.contrib.auth.middleware.LoginRequiredMiddleware"]
    with override_settings(MIDDLEWARE=middleware, **_SESSION_SETTINGS):
        if path == _ENDPOINT:
            response = (
                await _apost_initialize(AsyncClient())
                if mount
                else await sync_to_async(_post_initialize)(Client())
            )
        else:
            response = await _send(mount, "get", path)
    assert response.status_code == 200, response.content


# ----- what the auth backend is handed -----


def _logged_in_initialize(user: Any) -> Any:
    client = Client()
    client.force_login(user)
    return _post_initialize(client)


@pytest.mark.django_db(transaction=True)
async def test_a_session_user_is_not_the_caller_the_auth_backend_sees(mount: bool) -> None:
    """``AllowAnyBackend`` reads ``request.user``; a session cookie must not set it.

    The endpoint is CSRF-exempt, so a backend that took the session's user for
    the caller would let any page the user visits call tools as them. The
    session the ``initialize`` mints is owned by the shared anonymous principal,
    whoever the cookie names.
    """
    store = InMemorySessionStore()
    server = build_server()
    server._session_store = store  # noqa: SLF001 — the mount reads it at ``urls``
    user = await get_user_model().objects.acreate(username="cookie-holder")
    with override_settings(
        ROOT_URLCONF=urlconf_for(server, is_async=mount),
        MIDDLEWARE=_SESSION_MIDDLEWARE,
        **_SESSION_SETTINGS,
    ):
        if mount:
            client: Any = AsyncClient()
            await sync_to_async(client.force_login)(user)
            response = await _apost_initialize(client)
        else:
            response = await sync_to_async(_logged_in_initialize)(user)
    assert response.status_code == 200, response.content
    assert store.owner(response["Mcp-Session-Id"]) == "anonymous"


def test_the_sync_endpoint_reads_a_q_of_zero_as_django_does() -> None:
    """``application/json;q=0`` is refused exactly where Django reads it as refusing JSON.

    Django 5.2 reads a ``q`` of ``0`` as declaring a type unacceptable and 4.2
    ignores it, so the answer is pinned to ``request.accepts`` rather than to a
    version: a ``406`` where JSON is not accepted, served where it is. The
    lowest-versions job runs this on 4.2, the rest of the suite on 5.2.
    """
    accept = "application/json;q=0, text/event-stream"
    accepted = (
        RequestFactory().post(_ENDPOINT, headers={"Accept": accept}).accepts("application/json")
    )
    with override_settings(ROOT_URLCONF=urlconf_for(build_server())):
        response = Client().post(
            _ENDPOINT, _INITIALIZE, content_type="application/json", headers={"Accept": accept}
        )
    assert response.status_code == (200 if accepted else 406), response.content[:200]
