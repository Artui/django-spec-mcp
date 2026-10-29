from __future__ import annotations


def bearer_challenge(
    *,
    resource_metadata_url: str | None,
    scopes: list[str] | None = None,
    error: str | None = None,
) -> str:
    """Build the ``WWW-Authenticate`` value for a 401 or an insufficient-scope 403.

    A backend's ``www_authenticate_challenge`` returns this, so the format is
    decided here once. ``resource_metadata_url`` is required by name because it
    is the parameter a client reads to find the authorization server (RFC 9728):
    a challenge missing it fails with no error on either side, and ``None`` is
    the explicit way to say a backend has no metadata document to point at.
    Pass the URL the server's ``protected-resource-metadata`` route reverses to.

    The parts come in the order a client parses them: the realm, then the
    metadata pointer, then ``error``, then ``scope`` (the ``scopes`` joined by a
    space, which is how RFC 6750 spells a scope list). A part that is ``None``
    or empty is left out.
    """
    parts: list[str] = ['Bearer realm="mcp"']
    if resource_metadata_url:
        parts.append(f'resource_metadata="{resource_metadata_url}"')
    if error:
        parts.append(f'error="{error}"')
    if scopes:
        parts.append(f'scope="{" ".join(scopes)}"')
    return ", ".join(parts)


__all__ = ["bearer_challenge"]
