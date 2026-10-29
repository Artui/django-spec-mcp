"""The ``406`` body and the metadata document are ``json.dumps`` with its defaults.

``JsonResponse`` writes them, so they carry ``", "`` and ``": "`` separators and
escape non-ASCII characters. A client parsing JSON sees no difference; one
comparing bytes does, so the bytes are pinned here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient, Client, override_settings

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

_PRM = "/mcp/.well-known/oauth-protected-resource"
_METADATA = ProtectedResourceMetadata(
    resource="https://exämple.test/mcp", scopes_supported=["mcp:read"]
)


class _NonAsciiBackend(AllowAnyBackend):
    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        return _METADATA


async def _send(is_async: bool, method: str, path: str, accept: str) -> Any:
    server = MCPServer(
        name="bytes", auth_backend=_NonAsciiBackend(), session_store=InMemorySessionStore()
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        client: Any = AsyncClient() if is_async else Client()
        send = getattr(client, method)
        kwargs: dict[str, Any] = {"headers": {"Accept": accept}}
        if method == "post":
            kwargs.update(data="{}", content_type="application/json")
        if is_async:
            return await send(path, **kwargs)
        return await sync_to_async(send)(path, **kwargs)


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_the_metadata_document_is_json_dumps_with_its_defaults(is_async: bool) -> None:
    response = await _send(is_async, "get", _PRM, "*/*")
    assert response.status_code == 200
    assert response.content == json.dumps(_METADATA.to_dict()).encode()
    assert b'"resource": "https://ex\\u00e4mple.test/mcp"' in response.content


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("method", "path"), [("post", "/mcp/"), ("get", _PRM)], ids=["endpoint", "metadata"]
)
async def test_the_406_body_is_json_dumps_with_its_defaults(
    is_async: bool, method: str, path: str
) -> None:
    response = await _send(is_async, method, path, "text/html")
    assert response.status_code == 406
    assert response.content == b'{"detail": "Could not satisfy the request Accept header."}'
