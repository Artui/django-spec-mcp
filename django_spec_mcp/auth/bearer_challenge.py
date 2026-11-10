from __future__ import annotations


def bearer_challenge(
    *,
    resource_metadata_url: str | None,
    scopes: list[str] | None = None,
    error: str | None = None,
) -> str:
    """Build the ``WWW-Authenticate`` value for a 401 or an insufficient-scope 403."""
    parts: list[str] = ['Bearer realm="mcp"']
    if resource_metadata_url:
        parts.append(f'resource_metadata="{resource_metadata_url}"')
    if error:
        parts.append(f'error="{error}"')
    if scopes:
        parts.append(f'scope="{" ".join(scopes)}"')
    return ", ".join(parts)


__all__ = ["bearer_challenge"]
